"""
core/suscripcion.py
====================
Estado comercial de la instalacion: si esta al dia, en gracia, cortada
o dada de baja.

Este modulo es identico en todas las instalaciones. Lo unico que cambia
es CLIENTE_ID en el .env, que dice a que cliente corresponde esta copia.

TRES DECISIONES DE DISENO QUE CONVIENE CONOCER ANTES DE TOCAR ESTO
-------------------------------------------------------------------

1. El estado NO se guarda: se calcula.
   Lo que se guarda en la base es `paga_hasta` (ultimo dia cubierto por
   el pago) y el interruptor manual. El estado sale de la vista
   `v_estado_suscripcion`, que aplica la regla una sola vez y para todos
   los que la consulten. Si el estado fuera un campo guardado, alguien
   tendria que acordarse de cambiarlo el dia del vencimiento, y el dia
   que se olvide el sistema queda mintiendo.

2. La regla vive en SQL, no aca.
   El login, el panel y el resumen diario preguntan lo mismo a la misma
   vista. Si la logica del corte estuviera escrita en Python, tarde o
   temprano el mail diario y la pantalla iban a discrepar, y el sintoma
   seria un cliente cortado que igual sigue recibiendo el resumen de las
   siete de la manana.

3. Ante una falla, DEJA PASAR (no bloquea).
   Si MySQL no responde, si falta la vista o si el cliente no esta
   cargado en la tabla, este modulo devuelve ACTIVO y deja el motivo en
   el log. Es a proposito: un corte de base de datos nunca puede dejar
   afuera a un cliente que paga. El riesgo inverso -que un moroso siga
   entrando un dia mas- cuesta muchisimo menos que un gerente que no
   puede ver sus ventas por un problema nuestro.

Este archivo NO importa streamlit ni core.auth. Asi lo puede usar tanto
la aplicacion como scripts/daily_brief.py, que corre desde el
Programador de tareas sin ninguna interfaz abierta.
"""

from __future__ import annotations

import time
from datetime import date
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text

from core.config import settings


# =====================================================================
# Parametros
# =====================================================================

# Estados en los que la instalacion no se puede usar.
ESTADOS_BLOQUEANTES = ("SUSPENDIDO", "BAJA")

# Estado que se usa cuando no se pudo averiguar el verdadero.
ESTADO_POR_DEFECTO = "ACTIVO"

# Cuantos segundos se reutiliza el estado ya consultado antes de volver
# a preguntarle a la base.
#
# Sin cache, cada recarga de pantalla de Streamlit (que son muchas)
# dispara una consulta. Con una cache eterna, un corte hecho hoy recien
# tendria efecto cuando el usuario cierre sesion, que puede ser la
# semana que viene. Cinco minutos es el punto medio: no castiga la base
# y el corte se siente casi en el momento.
SEGUNDOS_DE_CACHE = 300

# Dias antes del vencimiento en que se empieza a avisar en pantalla.
DIAS_DE_AVISO_PREVIO = 7


# =====================================================================
# Conexion propia
#
# No se reusa el motor de core/auth.py a proposito: auth importa
# streamlit y este modulo tiene que poder usarse desde un script suelto.
# Ademas, si auth importara a suscripcion y suscripcion importara a auth,
# Python no podria resolver el circulo.
# =====================================================================

_engine = None


def _get_engine():
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


def _log(nivel: str, mensaje: str) -> None:
    """Escribe en la auditoria si se puede, sin arrastrar la app si falla."""
    try:
        from core.audit import write_log

        write_log(nivel, f"[suscripcion] {mensaje}")
    except Exception:  # noqa: BLE001
        pass


# =====================================================================
# Lectura del estado
# =====================================================================

_cache: dict[str, tuple[float, dict]] = {}


def cliente_id() -> str:
    """Identificador de esta instalacion, tomado del .env."""
    return (settings.cliente_id or "").strip().lower()


def _estado_desconocido(cliente: str, motivo: str) -> dict:
    """Estado que se devuelve cuando no se pudo consultar. Deja pasar."""
    return {
        "cliente_id": cliente,
        "razon_social": "",
        "plan": "",
        "estado_efectivo": ESTADO_POR_DEFECTO,
        "paga_hasta": None,
        "dias_para_vencer": None,
        "corta_el": None,
        "dias_gracia": None,
        "override_manual": "NINGUNO",
        "override_motivo": None,
        "mensaje_cliente": None,
        "contacto_nombre": None,
        "contacto_email": None,
        "contacto_telefono": None,
        "verificado": False,
        "motivo_sin_verificar": motivo,
    }


def estado(cliente: str | None = None, refrescar: bool = False) -> dict:
    """
    Devuelve el estado comercial de una instalacion.

    Siempre devuelve un diccionario utilizable, nunca None y nunca lanza
    excepcion. La clave `verificado` dice si el dato salio de la base
    (True) o si es el valor por defecto porque algo fallo (False).
    """
    cliente = (cliente or cliente_id()).strip().lower()

    if not cliente:
        return _estado_desconocido("", "CLIENTE_ID no esta configurado en el .env")

    if not refrescar:
        guardado = _cache.get(cliente)
        if guardado and (time.monotonic() - guardado[0]) < SEGUNDOS_DE_CACHE:
            return guardado[1]

    try:
        with _get_engine().connect() as conexion:
            fila = conexion.execute(
                text(
                    "SELECT cliente_id, razon_social, plan, paga_hasta, dias_gracia, "
                    "override_manual, override_motivo, mensaje_cliente, "
                    "contacto_nombre, contacto_email, contacto_telefono, "
                    "dias_para_vencer, corta_el, estado_efectivo "
                    "FROM v_estado_suscripcion WHERE cliente_id = :cliente"
                ),
                {"cliente": cliente},
            ).mappings().first()
    except Exception as error:  # noqa: BLE001
        resultado = _estado_desconocido(
            cliente, f"No se pudo consultar la base de suscripciones: {error}"
        )
        _log("WARNING", resultado["motivo_sin_verificar"])
        _cache[cliente] = (time.monotonic(), resultado)
        return resultado

    if fila is None:
        resultado = _estado_desconocido(
            cliente, f"El cliente '{cliente}' no esta cargado en la tabla clientes"
        )
        _log("WARNING", resultado["motivo_sin_verificar"])
        _cache[cliente] = (time.monotonic(), resultado)
        return resultado

    resultado = dict(fila)
    resultado["verificado"] = True
    resultado["motivo_sin_verificar"] = ""
    _cache[cliente] = (time.monotonic(), resultado)
    return resultado


def limpiar_cache(cliente: str | None = None) -> None:
    """Obliga a releer el estado en la proxima consulta."""
    if cliente:
        _cache.pop(cliente.strip().lower(), None)
    else:
        _cache.clear()


# =====================================================================
# Interpretacion del estado
# =====================================================================

def bloqueado(datos: dict | None = None) -> bool:
    """True si esta instalacion no se puede usar."""
    datos = datos or estado()
    return str(datos.get("estado_efectivo", "")).upper() in ESTADOS_BLOQUEANTES


def mensaje_de_bloqueo(datos: dict | None = None) -> str:
    """
    Texto que ve el usuario cuando la instalacion esta cortada.

    Es un mensaje comercial, no un error tecnico: quien lo lee es un
    gerente que quiere ver sus ventas, no el que decide los pagos.
    """
    datos = datos or estado()

    propio = (datos.get("mensaje_cliente") or "").strip()
    if propio:
        return propio

    if str(datos.get("estado_efectivo", "")).upper() == "BAJA":
        return (
            "El servicio de Conepasa IA para esta empresa está dado de baja. "
            "Si creés que es un error, comunicate con Conepasa."
        )

    return (
        "El servicio de Conepasa IA está temporalmente suspendido por una "
        "cuestión administrativa. Se reactiva apenas se regulariza. "
        "Tus datos y tu historial están intactos."
    )


def datos_de_contacto(datos: dict | None = None) -> str:
    """Linea de contacto para mostrar debajo del mensaje de corte."""
    datos = datos or estado()
    partes = [
        (datos.get("contacto_nombre") or "").strip(),
        (datos.get("contacto_telefono") or "").strip(),
        (datos.get("contacto_email") or "").strip(),
    ]
    return "  ·  ".join(parte for parte in partes if parte)


def aviso_en_pantalla(datos: dict | None = None) -> str:
    """
    Aviso para mostrar arriba de la pantalla cuando corresponde.

    Cadena vacia significa que no hay nada que avisar. Se avisa en dos
    situaciones: cuando faltan pocos dias para el vencimiento y cuando
    ya vencio pero sigue dentro de los dias de gracia.
    """
    datos = datos or estado()

    if not datos.get("verificado"):
        return ""

    estado_actual = str(datos.get("estado_efectivo", "")).upper()
    dias = datos.get("dias_para_vencer")

    if estado_actual == "GRACIA":
        corta = datos.get("corta_el")
        cuando = corta.strftime("%d/%m/%Y") if isinstance(corta, date) else "en breve"
        return (
            f"El pago de la suscripción está vencido. El servicio sigue "
            f"disponible hasta el {cuando}."
        )

    if estado_actual == "ACTIVO" and isinstance(dias, int):
        # Con el interruptor en FORZAR_ACTIVO la fecha no manda, asi que
        # avisar de un vencimiento que no va a cortar nada solo asusta.
        if str(datos.get("override_manual", "")).upper() == "FORZAR_ACTIVO":
            return ""
        if 0 <= dias <= DIAS_DE_AVISO_PREVIO:
            if dias == 0:
                return "La suscripción vence hoy."
            return f"La suscripción vence en {dias} día(s)."

    return ""


# =====================================================================
# Administracion (solo la usa el panel de Conepasa)
# =====================================================================

def registrar_evento(
    cliente: str,
    evento: str,
    estado_antes: str = "",
    estado_desp: str = "",
    detalle: str = "",
    usuario: str = "",
) -> None:
    """Deja constancia de todo movimiento comercial. Nunca lanza excepcion."""
    try:
        with _get_engine().begin() as conexion:
            conexion.execute(
                text(
                    "INSERT INTO suscripcion_eventos "
                    "(cliente_id, evento, estado_antes, estado_desp, detalle, usuario) "
                    "VALUES (:cliente, :evento, :antes, :desp, :detalle, :usuario)"
                ),
                {
                    "cliente": cliente,
                    "evento": evento[:60],
                    "antes": (estado_antes or "")[:20] or None,
                    "desp": (estado_desp or "")[:20] or None,
                    "detalle": (detalle or "")[:255] or None,
                    "usuario": (usuario or "")[:120] or None,
                },
            )
    except Exception as error:  # noqa: BLE001
        _log("WARNING", f"No se pudo registrar el evento '{evento}': {error}")


def listar_clientes() -> list[dict]:
    """Todas las instalaciones con su estado actual, para el panel."""
    try:
        with _get_engine().connect() as conexion:
            filas = conexion.execute(
                text(
                    "SELECT * FROM v_estado_suscripcion "
                    "ORDER BY FIELD(estado_efectivo,'SUSPENDIDO','GRACIA','ACTIVO','BAJA'), "
                    "razon_social"
                )
            ).mappings().all()
        return [dict(fila) for fila in filas]
    except Exception as error:  # noqa: BLE001
        _log("WARNING", f"No se pudo listar clientes: {error}")
        return []


def crear_cliente(
    cliente: str,
    razon_social: str,
    plan: str = "BASE",
    max_usuarios: int = 3,
    paga_hasta: date | None = None,
    dias_gracia: int = 3,
    ruc: str = "",
    contacto_nombre: str = "",
    contacto_email: str = "",
    contacto_telefono: str = "",
    instalador: str = "",
    hecho_por: str = "",
) -> tuple[bool, str]:
    """Da de alta una instalacion nueva junto con su estado inicial."""
    cliente = (cliente or "").strip().lower()
    if not cliente:
        return False, "Falta el identificador del cliente."
    if not (razon_social or "").strip():
        return False, "Falta la razón social."

    try:
        with _get_engine().begin() as conexion:
            conexion.execute(
                text(
                    "INSERT INTO clientes (cliente_id, razon_social, ruc, "
                    "contacto_nombre, contacto_email, contacto_telefono, plan, "
                    "max_usuarios, fecha_alta, instalador) "
                    "VALUES (:cliente, :razon, :ruc, :cnombre, :cemail, :ctel, "
                    ":plan, :maxu, CURDATE(), :instalador)"
                ),
                {
                    "cliente": cliente,
                    "razon": razon_social.strip(),
                    "ruc": ruc.strip() or None,
                    "cnombre": contacto_nombre.strip() or None,
                    "cemail": contacto_email.strip() or None,
                    "ctel": contacto_telefono.strip() or None,
                    "plan": plan,
                    "maxu": int(max_usuarios),
                    "instalador": instalador.strip() or None,
                },
            )
            conexion.execute(
                text(
                    "INSERT INTO suscripcion_estado "
                    "(cliente_id, paga_hasta, dias_gracia) "
                    "VALUES (:cliente, :paga_hasta, :gracia)"
                ),
                {
                    "cliente": cliente,
                    "paga_hasta": paga_hasta or date.today(),
                    "gracia": int(dias_gracia),
                },
            )
    except Exception as error:  # noqa: BLE001
        if "Duplicate" in str(error):
            return False, f"Ya existe un cliente con el identificador '{cliente}'."
        return False, f"No se pudo dar de alta: {error}"

    registrar_evento(
        cliente, "ALTA_CLIENTE", "", "ACTIVO",
        f"{razon_social.strip()} · plan {plan}", hecho_por,
    )
    limpiar_cache(cliente)
    return True, ""


def registrar_pago(
    cliente: str,
    periodo_desde: date,
    periodo_hasta: date,
    monto_gs: int,
    medio: str = "TRANSFERENCIA",
    fecha_pago: date | None = None,
    comprobante: str = "",
    observacion: str = "",
    hecho_por: str = "",
) -> tuple[bool, str]:
    """
    Registra un pago y adelanta `paga_hasta` al fin del periodo cubierto.

    Ademas apaga el interruptor manual si estaba puesto: si el cliente
    pago, no tiene sentido dejar un corte forzado vigente que lo siga
    bloqueando.
    """
    cliente = (cliente or "").strip().lower()
    if periodo_hasta < periodo_desde:
        return False, "El período termina antes de empezar. Revisá las fechas."

    anterior = estado(cliente, refrescar=True)

    try:
        with _get_engine().begin() as conexion:
            conexion.execute(
                text(
                    "INSERT INTO pagos (cliente_id, fecha_pago, periodo_desde, "
                    "periodo_hasta, monto_gs, medio, comprobante, observacion, "
                    "registrado_por) VALUES (:cliente, :fpago, :desde, :hasta, "
                    ":monto, :medio, :comprobante, :obs, :quien)"
                ),
                {
                    "cliente": cliente,
                    "fpago": fecha_pago or date.today(),
                    "desde": periodo_desde,
                    "hasta": periodo_hasta,
                    "monto": int(monto_gs or 0),
                    "medio": medio,
                    "comprobante": (comprobante or "").strip() or None,
                    "obs": (observacion or "").strip() or None,
                    "quien": (hecho_por or "").strip() or None,
                },
            )
            # GREATEST evita que cargar un pago viejo retroceda la fecha
            # de corte de un cliente que ya esta al dia.
            conexion.execute(
                text(
                    "UPDATE suscripcion_estado "
                    "SET paga_hasta = GREATEST(paga_hasta, :hasta), "
                    "    override_manual = CASE WHEN override_manual = 'FORZAR_SUSPENDIDO' "
                    "                           THEN 'NINGUNO' ELSE override_manual END "
                    "WHERE cliente_id = :cliente"
                ),
                {"hasta": periodo_hasta, "cliente": cliente},
            )
    except Exception as error:  # noqa: BLE001
        return False, f"No se pudo registrar el pago: {error}"

    limpiar_cache(cliente)
    nuevo = estado(cliente, refrescar=True)
    registrar_evento(
        cliente,
        "PAGO_REGISTRADO",
        anterior.get("estado_efectivo", ""),
        nuevo.get("estado_efectivo", ""),
        f"Gs. {int(monto_gs or 0):,}".replace(",", ".")
        + f" · cubre hasta {periodo_hasta.strftime('%d/%m/%Y')}",
        hecho_por,
    )
    return True, ""


def cambiar_override(
    cliente: str,
    override: str,
    motivo: str = "",
    vence: date | None = None,
    hecho_por: str = "",
) -> tuple[bool, str]:
    """
    Mueve el interruptor manual.

    override: 'NINGUNO' | 'FORZAR_ACTIVO' | 'FORZAR_SUSPENDIDO'
    """
    cliente = (cliente or "").strip().lower()
    if override not in ("NINGUNO", "FORZAR_ACTIVO", "FORZAR_SUSPENDIDO"):
        return False, "Valor de interruptor no reconocido."
    if override != "NINGUNO" and not (motivo or "").strip():
        return False, "Escribí el motivo. Sin motivo no se puede mover el interruptor."

    anterior = estado(cliente, refrescar=True)

    try:
        with _get_engine().begin() as conexion:
            conexion.execute(
                text(
                    "UPDATE suscripcion_estado SET override_manual = :override, "
                    "override_motivo = :motivo, override_por = :quien, "
                    "override_en = NOW(), override_vence = :vence "
                    "WHERE cliente_id = :cliente"
                ),
                {
                    "override": override,
                    "motivo": (motivo or "").strip()[:255] or None,
                    "quien": (hecho_por or "").strip()[:120] or None,
                    "vence": vence,
                    "cliente": cliente,
                },
            )
    except Exception as error:  # noqa: BLE001
        return False, f"No se pudo cambiar el interruptor: {error}"

    limpiar_cache(cliente)
    nuevo = estado(cliente, refrescar=True)
    etiquetas = {
        "NINGUNO": "INTERRUPTOR_LIBERADO",
        "FORZAR_ACTIVO": "REACTIVACION_MANUAL",
        "FORZAR_SUSPENDIDO": "CORTE_MANUAL",
    }
    registrar_evento(
        cliente,
        etiquetas[override],
        anterior.get("estado_efectivo", ""),
        nuevo.get("estado_efectivo", ""),
        (motivo or "").strip(),
        hecho_por,
    )
    return True, ""


def cambiar_mensaje(cliente: str, mensaje: str, hecho_por: str = "") -> tuple[bool, str]:
    """Cambia el texto que ve el usuario cuando la instalacion esta cortada."""
    cliente = (cliente or "").strip().lower()
    try:
        with _get_engine().begin() as conexion:
            conexion.execute(
                text(
                    "UPDATE suscripcion_estado SET mensaje_cliente = :mensaje "
                    "WHERE cliente_id = :cliente"
                ),
                {"mensaje": (mensaje or "").strip()[:255] or None, "cliente": cliente},
            )
    except Exception as error:  # noqa: BLE001
        return False, f"No se pudo guardar el mensaje: {error}"

    limpiar_cache(cliente)
    registrar_evento(cliente, "MENSAJE_CAMBIADO", "", "", (mensaje or "").strip(), hecho_por)
    return True, ""


def cambiar_dias_gracia(cliente: str, dias: int, hecho_por: str = "") -> tuple[bool, str]:
    """Cambia la tolerancia posterior al vencimiento de una instalacion."""
    cliente = (cliente or "").strip().lower()
    if not 0 <= int(dias) <= 60:
        return False, "Los días de gracia tienen que estar entre 0 y 60."
    try:
        with _get_engine().begin() as conexion:
            conexion.execute(
                text(
                    "UPDATE suscripcion_estado SET dias_gracia = :dias "
                    "WHERE cliente_id = :cliente"
                ),
                {"dias": int(dias), "cliente": cliente},
            )
    except Exception as error:  # noqa: BLE001
        return False, f"No se pudieron guardar los días de gracia: {error}"

    limpiar_cache(cliente)
    registrar_evento(cliente, "GRACIA_CAMBIADA", "", "", f"{int(dias)} día(s)", hecho_por)
    return True, ""


def dar_de_baja(cliente: str, motivo: str, hecho_por: str = "") -> tuple[bool, str]:
    """Baja definitiva. No borra nada: apaga la bandera `activo` del cliente."""
    cliente = (cliente or "").strip().lower()
    if not (motivo or "").strip():
        return False, "Escribí el motivo de la baja."

    anterior = estado(cliente, refrescar=True)
    try:
        with _get_engine().begin() as conexion:
            conexion.execute(
                text("UPDATE clientes SET activo = 0 WHERE cliente_id = :cliente"),
                {"cliente": cliente},
            )
    except Exception as error:  # noqa: BLE001
        return False, f"No se pudo dar de baja: {error}"

    limpiar_cache(cliente)
    registrar_evento(
        cliente, "BAJA", anterior.get("estado_efectivo", ""), "BAJA",
        motivo.strip(), hecho_por,
    )
    return True, ""


def reactivar_cliente(cliente: str, motivo: str = "", hecho_por: str = "") -> tuple[bool, str]:
    """Revierte una baja."""
    cliente = (cliente or "").strip().lower()
    try:
        with _get_engine().begin() as conexion:
            conexion.execute(
                text("UPDATE clientes SET activo = 1 WHERE cliente_id = :cliente"),
                {"cliente": cliente},
            )
    except Exception as error:  # noqa: BLE001
        return False, f"No se pudo reactivar: {error}"

    limpiar_cache(cliente)
    nuevo = estado(cliente, refrescar=True)
    registrar_evento(
        cliente, "REACTIVACION", "BAJA", nuevo.get("estado_efectivo", ""),
        (motivo or "").strip(), hecho_por,
    )
    return True, ""


def pagos_recientes(cliente: str, limite: int = 24) -> list[dict]:
    try:
        with _get_engine().connect() as conexion:
            filas = conexion.execute(
                text(
                    "SELECT fecha_pago, periodo_desde, periodo_hasta, monto_gs, "
                    "medio, comprobante, registrado_por FROM pagos "
                    "WHERE cliente_id = :cliente ORDER BY fecha_pago DESC, id DESC "
                    "LIMIT :limite"
                ),
                {"cliente": (cliente or "").strip().lower(), "limite": int(limite)},
            ).mappings().all()
        return [dict(fila) for fila in filas]
    except Exception:  # noqa: BLE001
        return []


def eventos_recientes(cliente: str = "", limite: int = 40) -> list[dict]:
    cliente = (cliente or "").strip().lower()
    consulta = (
        "SELECT cliente_id, ocurrido_en, evento, estado_antes, estado_desp, "
        "detalle, usuario FROM suscripcion_eventos "
    )
    parametros: dict = {"limite": int(limite)}
    if cliente:
        consulta += "WHERE cliente_id = :cliente "
        parametros["cliente"] = cliente
    consulta += "ORDER BY ocurrido_en DESC, id DESC LIMIT :limite"

    try:
        with _get_engine().connect() as conexion:
            filas = conexion.execute(text(consulta), parametros).mappings().all()
        return [dict(fila) for fila in filas]
    except Exception:  # noqa: BLE001
        return []
