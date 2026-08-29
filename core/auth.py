"""
core/auth.py
=============
Autenticacion, roles y auditoria de accesos para Conepasa IA.

Este modulo es identico en todas las instancias (Ekaru, Ejapo y clientes
nuevos). Lo unico que cambia entre instalaciones es el .env.

Guarda todo en la base `conepasa_auth`, separada de las bases del negocio,
con un usuario MySQL propio que no tiene ningun permiso sobre los datos
comerciales.

Las contrasenas se guardan con scrypt (libreria estandar de Python, sin
dependencias nuevas). La clave en claro nunca se escribe en ningun lado.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta
from urllib.parse import quote_plus

import streamlit as st
from sqlalchemy import create_engine, text

from core.config import settings
from core.version import APP_VERSION


# =====================================================================
# Parametros
# =====================================================================

# Costo del hasheo. n=16384 usa ~16 MB de memoria por verificacion:
# suficiente para que probar claves por fuerza bruta sea caro, y
# despreciable para un login normal.
SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_MAXMEM = 64 * 1024 * 1024
SALT_BYTES = 16

MAX_INTENTOS_FALLIDOS = 5
MINUTOS_DE_BLOQUEO = 15
LARGO_MINIMO_PASSWORD = 8

ROLES = ("admin", "gerencia", "operacion", "presupuestos")

ROLES_DESCRIPCION = {
    "admin": "Todo, incluida la administracion de usuarios",
    "gerencia": "Ventas, costos, margenes, finanzas y compras",
    "operacion": "Ventas y operacion, sin costos ni margenes",
    "presupuestos": "Solo el modulo presupuestador",
}

# Permisos derivados del rol.
#
# Desde la Fase 0.3 estos permisos SI se aplican de verdad. El detalle
# de que tablas y columnas puede tocar cada rol vive en core/permisos.py,
# que es la unica fuente de verdad de esa parte.
#
# NOTA SOBRE "administra_suscripciones": arranca en False para los CUATRO
# roles, incluido admin, y no se otorga nunca desde el rol. Se otorga con
# la bandera `es_operador` de la tabla usuarios, que solo se pone a mano
# con una consulta SQL.
#
# El motivo: el rol "admin" es el administrador DEL CLIENTE, el que da de
# alta a la gente de su propia empresa. Si el corte de servicio dependiera
# del rol, ese mismo admin podria crearse un usuario y levantarse su
# propio corte desde la pantalla de usuarios. La bandera no esta expuesta
# en ninguna pantalla, asi que no hay forma de otorgarsela desde adentro.
PERMISOS = {
    "admin": {
        "ve_costos": True,
        "ve_finanzas": True,
        "ve_compras": True,
        "usa_chat": True,
        "usa_lfl": True,
        "usa_presupuestador": True,
        "ve_detalle_tecnico": True,
        "administra_usuarios": True,
        "administra_sincronizacion": True,
        "administra_suscripciones": False,
    },
    "gerencia": {
        "ve_costos": True,
        "ve_finanzas": True,
        "ve_compras": True,
        "usa_chat": True,
        "usa_lfl": True,
        "usa_presupuestador": True,
        "ve_detalle_tecnico": True,
        "administra_usuarios": False,
        "administra_sincronizacion": False,
        "administra_suscripciones": False,
    },
    "operacion": {
        "ve_costos": False,
        "ve_finanzas": False,
        "ve_compras": False,
        "usa_chat": True,
        "usa_lfl": True,
        "usa_presupuestador": False,
        # El SQL crudo y el historial de auditoria muestran nombres de
        # tablas y preguntas de otros usuarios. No es para este rol.
        "ve_detalle_tecnico": False,
        "administra_usuarios": False,
        "administra_sincronizacion": False,
        "administra_suscripciones": False,
    },
    "presupuestos": {
        "ve_costos": False,
        "ve_finanzas": False,
        "ve_compras": False,
        "usa_chat": False,
        "usa_lfl": False,
        "usa_presupuestador": True,
        "ve_detalle_tecnico": False,
        "administra_usuarios": False,
        "administra_sincronizacion": False,
        "administra_suscripciones": False,
    },
}

CLAVE_SESION = "conepasa_usuario"
CLAVE_EXPIRA = "conepasa_sesion_expira"


# =====================================================================
# Conexion a la base de autenticacion
# =====================================================================

_engine = None


def get_auth_engine():
    """Motor de conexion a conepasa_auth. Se crea una sola vez por proceso."""
    global _engine
    if _engine is None:
        usuario = quote_plus(settings.auth_mysql_user)
        password = quote_plus(settings.auth_mysql_password)
        url = (
            f"mysql+pymysql://{usuario}:{password}@{settings.mysql_host}:"
            f"{settings.mysql_port}/{settings.auth_database}?charset=utf8mb4"
        )
        _engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_recycle=300,
            connect_args={"read_timeout": 15, "write_timeout": 15},
        )
    return _engine


def probar_conexion() -> tuple[bool, str]:
    """Verifica que la base de usuarios este accesible."""
    try:
        with get_auth_engine().connect() as conexion:
            conexion.execute(text("SELECT 1 FROM usuarios LIMIT 1"))
        return True, "Conectado"
    except Exception as error:
        return False, str(error)


# =====================================================================
# Hasheo de contrasenas
# =====================================================================

def hashear_password(password: str) -> str:
    """Devuelve el hash listo para guardar. Formato: scrypt$n$r$p$salt$hash"""
    salt = secrets.token_bytes(SALT_BYTES)
    derivada = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=SCRYPT_MAXMEM,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${derivada.hex()}"


def verificar_password(password: str, almacenado: str) -> bool:
    """Compara una clave tipeada contra el hash guardado."""
    try:
        algoritmo, n, r, p, salt_hex, hash_hex = str(almacenado).split("$")
        if algoritmo != "scrypt":
            return False
        esperado = bytes.fromhex(hash_hex)
        derivada = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(esperado),
            maxmem=SCRYPT_MAXMEM,
        )
        return secrets.compare_digest(derivada, esperado)
    except Exception:
        return False


def validar_password_nueva(password: str, repetida: str) -> str:
    """Devuelve un mensaje de error, o cadena vacia si la clave es aceptable."""
    if not password:
        return "Ingresá una contraseña."
    if password != repetida:
        return "Las dos contraseñas no coinciden."
    if len(password) < LARGO_MINIMO_PASSWORD:
        return f"La contraseña tiene que tener al menos {LARGO_MINIMO_PASSWORD} caracteres."
    if password.strip() != password:
        return "La contraseña no puede empezar ni terminar con espacios."
    return ""


# =====================================================================
# Auditoria
# =====================================================================

def _identificador_sesion() -> str | None:
    """Identificador corto de la sesion de Streamlit, si se puede obtener."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        contexto = get_script_run_ctx()
        return contexto.session_id[:45] if contexto else None
    except Exception:
        return None


def registrar_acceso(usuario: str, evento: str, detalle: str | None = None) -> None:
    """
    Deja constancia en la tabla `accesos`.

    Nunca lanza excepcion: un fallo de auditoria no debe impedir que
    alguien entre o salga del sistema.
    """
    try:
        with get_auth_engine().begin() as conexion:
            conexion.execute(
                text(
                    "INSERT INTO accesos (usuario, evento, detalle, origen) "
                    "VALUES (:usuario, :evento, :detalle, :origen)"
                ),
                {
                    "usuario": (usuario or "")[:60],
                    "evento": evento,
                    "detalle": (detalle or None) and str(detalle)[:255],
                    "origen": _identificador_sesion(),
                },
            )
    except Exception:
        pass


def accesos_recientes(limite: int = 30) -> list[dict]:
    """Ultimos eventos de acceso, para el panel de administracion."""
    try:
        with get_auth_engine().connect() as conexion:
            filas = conexion.execute(
                text(
                    "SELECT usuario, evento, detalle, creado_el "
                    "FROM accesos ORDER BY creado_el DESC LIMIT :limite"
                ),
                {"limite": int(limite)},
            ).mappings().all()
        return [dict(fila) for fila in filas]
    except Exception:
        return []


# =====================================================================
# Autenticacion
# =====================================================================

def _texto_a_lista(valor) -> list[str]:
    if not valor:
        return []
    return [parte.strip() for parte in str(valor).split(",") if parte.strip()]


def _armar_sesion(fila) -> dict:
    rol = str(fila["rol"])
    permisos = dict(PERMISOS.get(rol, PERMISOS["operacion"]))

    # Bandera de personal de Conepasa. Se lee con .get porque no todas las
    # consultas la traen y porque una instalacion vieja puede todavia no
    # tener la columna: en ese caso vale 0 y nadie ve el panel comercial,
    # que es exactamente el comportamiento seguro.
    try:
        es_operador = bool(fila["es_operador"])
    except (KeyError, TypeError):
        es_operador = False

    if es_operador:
        permisos["administra_suscripciones"] = True

    return {
        "id": int(fila["id"]),
        "usuario": str(fila["usuario"]),
        "nombre": str(fila["nombre"]),
        "email": fila["email"],
        "rol": rol,
        "empresas": _texto_a_lista(fila["empresas"]),
        "sucursales": _texto_a_lista(fila["sucursales"]),
        "debe_cambiar_password": bool(fila["debe_cambiar_password"]),
        "es_operador": es_operador,
        "permisos": permisos,
    }


def _sumar_intento_fallido(usuario: str, intentos_actuales: int) -> None:
    intentos = int(intentos_actuales or 0) + 1
    bloquear = intentos >= MAX_INTENTOS_FALLIDOS
    try:
        with get_auth_engine().begin() as conexion:
            conexion.execute(
                text(
                    "UPDATE usuarios SET intentos_fallidos = :intentos, "
                    "bloqueado_hasta = :bloqueo WHERE usuario = :usuario"
                ),
                {
                    "intentos": 0 if bloquear else intentos,
                    "bloqueo": (
                        datetime.now() + timedelta(minutes=MINUTOS_DE_BLOQUEO)
                        if bloquear
                        else None
                    ),
                    "usuario": usuario,
                },
            )
    except Exception:
        pass

    if bloquear:
        registrar_acceso(
            usuario,
            "bloqueo",
            f"{MAX_INTENTOS_FALLIDOS} intentos fallidos. Bloqueado {MINUTOS_DE_BLOQUEO} minutos.",
        )
    else:
        registrar_acceso(usuario, "login_fallido", f"Intento {intentos} de {MAX_INTENTOS_FALLIDOS}")


def _marcar_acceso_correcto(usuario: str) -> None:
    try:
        with get_auth_engine().begin() as conexion:
            conexion.execute(
                text(
                    "UPDATE usuarios SET ultimo_acceso = NOW(), intentos_fallidos = 0, "
                    "bloqueado_hasta = NULL WHERE usuario = :usuario"
                ),
                {"usuario": usuario},
            )
    except Exception:
        pass


def autenticar(usuario: str, password: str) -> tuple[dict | None, str]:
    """
    Valida usuario y contrasena.

    Devuelve (datos_del_usuario, "") si esta todo bien,
    o (None, "mensaje de error") si no.
    """
    usuario = (usuario or "").strip().lower()
    if not usuario or not password:
        return None, "Ingresá usuario y contraseña."

    try:
        with get_auth_engine().connect() as conexion:
            fila = conexion.execute(
                text(
                    "SELECT id, usuario, nombre, email, password_hash, rol, empresas, "
                    "sucursales, activo, debe_cambiar_password, intentos_fallidos, "
                    "bloqueado_hasta, es_operador FROM usuarios WHERE usuario = :usuario"
                ),
                {"usuario": usuario},
            ).mappings().first()
    except Exception as error:
        return None, (
            "No se pudo conectar a la base de usuarios. "
            f"Revisá AUTH_MYSQL_USER y AUTH_MYSQL_PASSWORD en el .env.\n\n{error}"
        )

    if fila is None:
        registrar_acceso(usuario, "login_fallido", "Usuario inexistente")
        return None, "Usuario o contraseña incorrectos."

    if not int(fila["activo"] or 0):
        registrar_acceso(usuario, "login_fallido", "Cuenta desactivada")
        return None, "Esta cuenta está desactivada. Consultá con el administrador."

    bloqueado_hasta = fila["bloqueado_hasta"]
    if bloqueado_hasta and bloqueado_hasta > datetime.now():
        restantes = int((bloqueado_hasta - datetime.now()).total_seconds() // 60) + 1
        return None, (
            f"Cuenta bloqueada por intentos fallidos. "
            f"Volvé a intentar en {restantes} minuto(s)."
        )

    if not verificar_password(password, fila["password_hash"]):
        _sumar_intento_fallido(usuario, fila["intentos_fallidos"])
        return None, "Usuario o contraseña incorrectos."

    _marcar_acceso_correcto(usuario)
    registrar_acceso(usuario, "login_ok")
    return _armar_sesion(fila), ""


def cambiar_password(usuario: str, password_nueva: str) -> tuple[bool, str]:
    """Guarda una contrasena nueva y baja la bandera de cambio obligatorio."""
    try:
        with get_auth_engine().begin() as conexion:
            conexion.execute(
                text(
                    "UPDATE usuarios SET password_hash = :hash, "
                    "debe_cambiar_password = 0, intentos_fallidos = 0, "
                    "bloqueado_hasta = NULL WHERE usuario = :usuario"
                ),
                {"hash": hashear_password(password_nueva), "usuario": usuario},
            )
    except Exception as error:
        return False, f"No se pudo guardar la contraseña nueva: {error}"

    registrar_acceso(usuario, "password_cambiada")
    return True, ""


# =====================================================================
# Sesion de Streamlit
# =====================================================================

# Claves de st.session_state que guardan la conversacion del chat.
# app.py las nombra "messages_{empresa}" y "debug_{empresa}".
_PREFIJOS_DE_CONVERSACION = ("messages_", "debug_")


def limpiar_estado_de_conversacion() -> None:
    """
    Borra el historial de chat de la sesion del navegador.

    Se llama al abrir Y al cerrar sesion. Sin esto, el que entra despues
    hereda la conversacion del que estaba antes en la misma computadora:
    veria sus preguntas y el agente arrastraria ese contexto a las
    respuestas nuevas. Es una fuga entre usuarios, no una molestia.
    """
    claves = [
        clave
        for clave in list(st.session_state.keys())
        if str(clave).startswith(_PREFIJOS_DE_CONVERSACION)
    ]
    for clave in claves:
        st.session_state.pop(clave, None)


def usuario_actual() -> dict | None:
    """Datos del usuario logueado, o None si la sesion no es valida."""
    datos = st.session_state.get(CLAVE_SESION)
    expira = st.session_state.get(CLAVE_EXPIRA)
    if not datos or not expira or datetime.now() >= expira:
        return None
    return datos


def cerrar_sesion() -> None:
    datos = st.session_state.get(CLAVE_SESION)
    if datos:
        registrar_acceso(datos["usuario"], "logout")
    st.session_state.pop(CLAVE_SESION, None)
    st.session_state.pop(CLAVE_EXPIRA, None)
    limpiar_estado_de_conversacion()


def _abrir_sesion(datos: dict) -> None:
    # Tambien al entrar, no solo al salir: si la sesion anterior vencio
    # sin que nadie apretara "Cerrar sesion", el historial sigue vivo en
    # el navegador y lo heredaria el que entre ahora.
    if st.session_state.get(CLAVE_SESION, {}).get("usuario") != datos.get("usuario"):
        limpiar_estado_de_conversacion()
    st.session_state[CLAVE_SESION] = datos
    st.session_state[CLAVE_EXPIRA] = datetime.now() + timedelta(
        hours=max(1, settings.auth_session_horas)
    )


def _pantalla_login() -> None:
    izquierda, centro, derecha = st.columns([1, 1.7, 1])
    with centro:
        st.markdown("## 🤖 Conepasa IA")
        st.caption(f"Ingresá con tu usuario para continuar. · v{APP_VERSION}")

        with st.form("formulario_login", clear_on_submit=False):
            usuario = st.text_input("Usuario", key="campo_login_usuario")
            password = st.text_input(
                "Contraseña", type="password", key="campo_login_password"
            )
            enviado = st.form_submit_button(
                "Ingresar", type="primary", use_container_width=True
            )

        if enviado:
            datos, error = autenticar(usuario, password)
            if error:
                st.error(error)
            else:
                _abrir_sesion(datos)
                st.rerun()

        st.divider()
        st.caption(
            "Si olvidaste tu contraseña, pedile al administrador del sistema "
            "que te la restablezca. Nadie puede verla, solo reemplazarla."
        )


def _pantalla_cambio_password(datos: dict) -> None:
    izquierda, centro, derecha = st.columns([1, 1.7, 1])
    with centro:
        st.markdown("## 🔑 Cambiá tu contraseña")
        st.info(
            f"Hola {datos['nombre']}. Estás usando una contraseña provisoria. "
            "Antes de continuar, elegí una nueva que solo vos conozcas."
        )

        with st.form("formulario_cambio_password", clear_on_submit=False):
            nueva = st.text_input(
                "Contraseña nueva", type="password", key="campo_password_nueva"
            )
            repetida = st.text_input(
                "Repetí la contraseña nueva", type="password", key="campo_password_repetida"
            )
            enviado = st.form_submit_button(
                "Guardar y continuar", type="primary", use_container_width=True
            )

        st.caption(f"Mínimo {LARGO_MINIMO_PASSWORD} caracteres.")

        if enviado:
            error = validar_password_nueva(nueva, repetida)
            if error:
                st.error(error)
            else:
                guardado, mensaje = cambiar_password(datos["usuario"], nueva)
                if not guardado:
                    st.error(mensaje)
                else:
                    datos["debe_cambiar_password"] = False
                    _abrir_sesion(datos)
                    st.success("Contraseña actualizada.")
                    st.rerun()

        st.divider()
        if st.button("Salir", use_container_width=True, key="boton_salir_cambio"):
            cerrar_sesion()
            st.rerun()


def _pantalla_servicio_suspendido(datos: dict, estado_suscripcion: dict) -> None:
    """
    Pantalla que ve el usuario cuando la suscripcion esta cortada.

    Es un mensaje comercial y no un error tecnico a proposito: quien la
    lee es casi siempre un gerente que queria ver sus ventas, no el que
    decide los pagos de la empresa. Un "acceso denegado" lo deja pensando
    que el sistema se rompio y genera un llamado que no hace falta.
    """
    from core import suscripcion

    izquierda, centro, derecha = st.columns([1, 1.7, 1])
    with centro:
        st.markdown("## 🤖 Conepasa IA")
        st.warning(suscripcion.mensaje_de_bloqueo(estado_suscripcion))

        contacto = suscripcion.datos_de_contacto(estado_suscripcion)
        if contacto:
            st.caption(f"Contacto: {contacto}")

        st.divider()
        if st.button("Salir", use_container_width=True, key="boton_salir_suspendido"):
            cerrar_sesion()
            st.rerun()


def exigir_login() -> dict:
    """
    Compuerta de acceso.

    Se llama al principio de app.py, despues de st.set_page_config.
    Si no hay sesion valida, dibuja el login y corta la ejecucion.
    Si hay sesion valida, devuelve los datos del usuario.
    """
    datos = usuario_actual()

    if datos is None:
        if st.session_state.get(CLAVE_SESION):
            st.session_state.pop(CLAVE_SESION, None)
            st.session_state.pop(CLAVE_EXPIRA, None)
            limpiar_estado_de_conversacion()
            st.warning("Tu sesión expiró. Ingresá de nuevo.")
        _pantalla_login()
        st.stop()

    if datos.get("debe_cambiar_password"):
        _pantalla_cambio_password(datos)
        st.stop()

    # --- Estado comercial de la instalacion ---------------------------
    #
    # Va DESPUES del login y no antes, para que el corte quede registrado
    # con nombre y apellido de quien intento entrar, y para que la
    # pantalla de suspension pueda mostrar un boton de salir.
    #
    # Se verifica en cada recarga, no solo al ingresar. Con una sesion de
    # doce horas, verificar unicamente en el login significa que un corte
    # hecho hoy recien tendria efecto manana. La consulta usa cache de
    # cinco minutos, asi que esto no castiga la base.
    #
    # El personal de Conepasa (es_operador) nunca queda afuera: si no,
    # una suspension mal puesta te dejaria sin forma de entrar a
    # levantarla desde la misma pantalla que la puso.
    try:
        from core import suscripcion

        estado_suscripcion = suscripcion.estado()
        if suscripcion.bloqueado(estado_suscripcion) and not datos.get("es_operador"):
            registrar_acceso(
                datos["usuario"],
                "acceso_bloqueado",
                f"Suscripción en estado {estado_suscripcion.get('estado_efectivo')}",
            )
            _pantalla_servicio_suspendido(datos, estado_suscripcion)
            st.stop()
        st.session_state["conepasa_estado_suscripcion"] = estado_suscripcion
    except Exception:  # noqa: BLE001
        # Si el control falla por un problema nuestro, se deja pasar. Un
        # error de base de datos jamas puede dejar afuera a un cliente que
        # esta al dia. Ver core/suscripcion.py para el razonamiento.
        st.session_state.pop("conepasa_estado_suscripcion", None)

    return datos


def bloque_usuario_en_sidebar(datos: dict) -> None:
    """Ficha del usuario y boton de salida. Se llama dentro de `with st.sidebar`."""
    st.markdown(f"**{datos['nombre']}**")
    st.caption(f"{ROLES_DESCRIPCION.get(datos['rol'], datos['rol'])}  ·  `{datos['rol']}`")
    if st.button("Cerrar sesión", use_container_width=True, key="boton_cerrar_sesion"):
        cerrar_sesion()
        st.rerun()


# =====================================================================
# Alcance de empresas y sucursales
# =====================================================================

def filtrar_empresas(datos: dict, empresas: dict) -> dict:
    """
    Deja solo las empresas que el usuario tiene asignadas.
    Lista vacia en el campo `empresas` significa "todas".
    """
    permitidas = datos.get("empresas") or []
    if not permitidas:
        return dict(empresas)
    return {clave: valor for clave, valor in empresas.items() if clave in permitidas}


def filtrar_sucursales(datos: dict, sucursales: list[str]) -> list[str]:
    """
    Deja solo las sucursales que el usuario tiene asignadas.
    Lista vacia significa "todas".
    """
    permitidas = datos.get("sucursales") or []
    if not permitidas:
        return list(sucursales)
    permitidas_normalizadas = {valor.strip().lower() for valor in permitidas}
    return [
        sucursal
        for sucursal in sucursales
        if str(sucursal).strip().lower() in permitidas_normalizadas
    ]


def puede(datos: dict, permiso: str) -> bool:
    """Consulta puntual de un permiso del rol."""
    return bool((datos.get("permisos") or {}).get(permiso, False))


# =====================================================================
# Administracion de usuarios (Fase 0.4)
#
# Reglas que este modulo hace cumplir siempre, esten o no en la pantalla:
#   1. No se borran usuarios, se desactivan. Borrar dejaria la tabla de
#      accesos con eventos de alguien que ya no existe y se perderia la
#      trazabilidad.
#   2. Un admin no puede desactivarse a si mismo ni quitarse su propio
#      rol de admin.
#   3. Nunca puede quedar la instalacion sin ningun admin activo.
# Sin la 2 y la 3, un clic distraido deja a todos afuera y hay que
# arreglarlo desde la ventana de comandos.
# =====================================================================

_PATRON_USUARIO = re.compile(r"^[a-z0-9][a-z0-9._-]{2,59}$")

# Silabas simples para armar claves provisorias que se puedan dictar por
# telefono sin tener que deletrear. Se evitan las que suenan parecido.
_SILABAS = (
    "ba", "be", "bo", "ca", "co", "cu", "da", "de", "do", "fa", "fe",
    "gu", "la", "le", "lo", "ma", "me", "mi", "mo", "na", "ne", "no",
    "pa", "pe", "pi", "ro", "sa", "se", "so", "ta", "te", "to", "tu",
)


def generar_password_provisoria() -> str:
    """
    Clave provisoria facil de dictar y de tipear en un celular.

    Tres silabas mas tres numeros: 'tumaro472'. Nueve caracteres, sin
    mayusculas ni simbolos, porque quien la recibe la va a escuchar por
    telefono y la va a escribir una sola vez: la cambia obligatoriamente
    al entrar.
    """
    silabas = "".join(secrets.choice(_SILABAS) for _ in range(3))
    numeros = "".join(secrets.choice("23456789") for _ in range(3))
    return f"{silabas}{numeros}"


def validar_nombre_de_usuario(usuario: str) -> str:
    """Mensaje de error, o cadena vacia si el nombre es aceptable."""
    usuario = (usuario or "").strip().lower()
    if not usuario:
        return "Ingresá un nombre de acceso."
    if not _PATRON_USUARIO.match(usuario):
        return (
            "El nombre de acceso tiene que empezar con letra o número, "
            "tener entre 3 y 60 caracteres, y usar solamente minúsculas, "
            "números, punto, guion o guion bajo. Sin espacios ni tildes."
        )
    return ""


def listar_usuarios() -> list[dict]:
    """Todos los usuarios, activos e inactivos, ordenados por nombre de acceso."""
    try:
        with get_auth_engine().connect() as conexion:
            filas = conexion.execute(
                text(
                    "SELECT id, usuario, nombre, email, rol, empresas, sucursales, "
                    "activo, debe_cambiar_password, intentos_fallidos, "
                    "bloqueado_hasta, ultimo_acceso, creado_el, es_operador "
                    "FROM usuarios ORDER BY usuario"
                )
            ).mappings().all()
        return [dict(fila) for fila in filas]
    except Exception:
        return []


def obtener_usuario(usuario: str) -> dict | None:
    try:
        with get_auth_engine().connect() as conexion:
            fila = conexion.execute(
                text(
                    "SELECT id, usuario, nombre, email, rol, empresas, sucursales, "
                    "activo, debe_cambiar_password, intentos_fallidos, "
                    "bloqueado_hasta, ultimo_acceso FROM usuarios WHERE usuario = :usuario"
                ),
                {"usuario": (usuario or "").strip().lower()},
            ).mappings().first()
        return dict(fila) if fila else None
    except Exception:
        return None


def _cantidad_de_admins_activos(excepto: str = "") -> int:
    """Cuantos admins activos hay, sin contar al que se pasa en `excepto`."""
    try:
        with get_auth_engine().connect() as conexion:
            return int(
                conexion.execute(
                    text(
                        "SELECT COUNT(*) FROM usuarios "
                        "WHERE rol = 'admin' AND activo = 1 AND usuario <> :excepto"
                    ),
                    {"excepto": (excepto or "").strip().lower()},
                ).scalar()
                or 0
            )
    except Exception:
        # Ante la duda, se asume que no hay otro admin: es el lado
        # seguro, porque bloquea la operacion en vez de permitirla.
        return 0


def crear_usuario(
    usuario: str,
    nombre: str,
    rol: str,
    password: str,
    email: str = "",
    empresas: str = "",
    sucursales: str = "",
    hecho_por: str = "",
) -> tuple[bool, str]:
    usuario = (usuario or "").strip().lower()

    error = validar_nombre_de_usuario(usuario)
    if error:
        return False, error
    if not (nombre or "").strip():
        return False, "Ingresá el nombre y apellido."
    if rol not in ROLES:
        return False, "El rol elegido no es válido."
    if obtener_usuario(usuario):
        return False, f"Ya existe un usuario '{usuario}'."

    try:
        with get_auth_engine().begin() as conexion:
            conexion.execute(
                text(
                    "INSERT INTO usuarios "
                    "(usuario, nombre, email, password_hash, rol, empresas, "
                    " sucursales, activo, debe_cambiar_password) "
                    "VALUES (:usuario, :nombre, :email, :hash, :rol, :empresas, "
                    "        :sucursales, 1, 1)"
                ),
                {
                    "usuario": usuario,
                    "nombre": nombre.strip(),
                    "email": (email or "").strip() or None,
                    "hash": hashear_password(password),
                    "rol": rol,
                    "empresas": (empresas or "").strip(),
                    "sucursales": (sucursales or "").strip() or None,
                },
            )
    except Exception as error_bd:
        return False, f"No se pudo crear el usuario: {error_bd}"

    registrar_acceso(usuario, "usuario_creado", f"rol={rol} por={hecho_por}")
    return True, ""


def actualizar_usuario(
    usuario: str,
    nombre: str,
    rol: str,
    email: str = "",
    empresas: str = "",
    sucursales: str = "",
    hecho_por: str = "",
) -> tuple[bool, str]:
    usuario = (usuario or "").strip().lower()
    actual = obtener_usuario(usuario)
    if not actual:
        return False, f"No existe el usuario '{usuario}'."
    if not (nombre or "").strip():
        return False, "Ingresá el nombre y apellido."
    if rol not in ROLES:
        return False, "El rol elegido no es válido."

    # Regla 2: nadie se quita a si mismo el rol de admin.
    quitandose_admin = (
        usuario == (hecho_por or "").strip().lower()
        and actual["rol"] == "admin"
        and rol != "admin"
    )
    if quitandose_admin:
        return False, (
            "No podés quitarte a vos mismo el rol de administrador. "
            "Pedile a otro administrador que lo haga."
        )

    # Regla 3: no dejar la instalacion sin ningun admin.
    if actual["rol"] == "admin" and rol != "admin":
        if _cantidad_de_admins_activos(excepto=usuario) == 0:
            return False, (
                "Es el único administrador activo del sistema. "
                "Creá otro administrador antes de cambiarle el rol."
            )

    try:
        with get_auth_engine().begin() as conexion:
            conexion.execute(
                text(
                    "UPDATE usuarios SET nombre = :nombre, email = :email, "
                    "rol = :rol, empresas = :empresas, sucursales = :sucursales "
                    "WHERE usuario = :usuario"
                ),
                {
                    "nombre": nombre.strip(),
                    "email": (email or "").strip() or None,
                    "rol": rol,
                    "empresas": (empresas or "").strip(),
                    "sucursales": (sucursales or "").strip() or None,
                    "usuario": usuario,
                },
            )
    except Exception as error_bd:
        return False, f"No se pudo guardar: {error_bd}"

    registrar_acceso(usuario, "usuario_modificado", f"rol={rol} por={hecho_por}")
    return True, ""


def restablecer_password(
    usuario: str, password_nueva: str, hecho_por: str = ""
) -> tuple[bool, str]:
    """
    Reemplaza la clave por una provisoria y obliga a cambiarla al entrar.

    Nadie, ni el administrador, puede VER una clave existente: solo
    reemplazarla. Es lo que permite decirle a un cliente que su
    contraseña no la conoce nadie.
    """
    usuario = (usuario or "").strip().lower()
    if not obtener_usuario(usuario):
        return False, f"No existe el usuario '{usuario}'."

    try:
        with get_auth_engine().begin() as conexion:
            conexion.execute(
                text(
                    "UPDATE usuarios SET password_hash = :hash, "
                    "debe_cambiar_password = 1, intentos_fallidos = 0, "
                    "bloqueado_hasta = NULL WHERE usuario = :usuario"
                ),
                {"hash": hashear_password(password_nueva), "usuario": usuario},
            )
    except Exception as error_bd:
        return False, f"No se pudo restablecer la contraseña: {error_bd}"

    registrar_acceso(usuario, "usuario_modificado", f"Contraseña restablecida por={hecho_por}")
    return True, ""


def cambiar_estado(usuario: str, activo: bool, hecho_por: str = "") -> tuple[bool, str]:
    usuario = (usuario or "").strip().lower()
    actual = obtener_usuario(usuario)
    if not actual:
        return False, f"No existe el usuario '{usuario}'."

    if not activo:
        # Regla 2: nadie se desactiva a si mismo.
        if usuario == (hecho_por or "").strip().lower():
            return False, (
                "No podés desactivar tu propio usuario. "
                "Pedile a otro administrador que lo haga."
            )
        # Regla 3: no dejar la instalacion sin ningun admin.
        if actual["rol"] == "admin" and _cantidad_de_admins_activos(excepto=usuario) == 0:
            return False, (
                "Es el único administrador activo del sistema. "
                "Creá otro administrador antes de desactivarlo."
            )

    try:
        with get_auth_engine().begin() as conexion:
            conexion.execute(
                text("UPDATE usuarios SET activo = :activo WHERE usuario = :usuario"),
                {"activo": 1 if activo else 0, "usuario": usuario},
            )
    except Exception as error_bd:
        return False, f"No se pudo cambiar el estado: {error_bd}"

    registrar_acceso(
        usuario,
        "usuario_modificado" if activo else "usuario_desactivado",
        f"{'Activado' if activo else 'Desactivado'} por={hecho_por}",
    )
    return True, ""


def desbloquear(usuario: str, hecho_por: str = "") -> tuple[bool, str]:
    """Limpia el bloqueo por intentos fallidos, sin tocar la contraseña."""
    usuario = (usuario or "").strip().lower()
    try:
        with get_auth_engine().begin() as conexion:
            resultado = conexion.execute(
                text(
                    "UPDATE usuarios SET intentos_fallidos = 0, bloqueado_hasta = NULL "
                    "WHERE usuario = :usuario"
                ),
                {"usuario": usuario},
            )
    except Exception as error_bd:
        return False, f"No se pudo desbloquear: {error_bd}"

    if not resultado.rowcount:
        return False, f"No existe el usuario '{usuario}'."

    registrar_acceso(usuario, "usuario_modificado", f"Desbloqueado por={hecho_por}")
    return True, ""


def esta_bloqueado(datos: dict) -> bool:
    bloqueado_hasta = datos.get("bloqueado_hasta")
    return bool(bloqueado_hasta and bloqueado_hasta > datetime.now())
