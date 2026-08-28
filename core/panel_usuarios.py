"""
core/panel_usuarios.py
=======================
Pantalla de administracion de usuarios. Solo la ve el rol admin.

Reemplaza a la ventana de comandos: antes, para dar de alta a alguien en
la instalacion de un cliente, habia que abrir gestionar_usuarios.bat
delante del gerente. Ese script sigue existiendo y es la salida de
emergencia si alguna vez nadie puede entrar al sistema.

Todas las reglas de seguridad (no borrar, no dejarse afuera, no dejar la
instalacion sin admin) viven en core/auth.py, no aca. Esta pantalla solo
muestra lo que auth.py devuelve. Asi las reglas se cumplen igual si
manana se agrega otra forma de administrar usuarios.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import auth


# Clave donde se guarda la contrasena provisoria recien generada, para
# poder mostrarla despues del rerun. Se borra apenas el admin la cierra.
_CLAVE_PROVISORIA = "panel_usuarios_password_generada"


def _texto_estado(usuario: dict) -> str:
    if not usuario["activo"]:
        return "Desactivado"
    if auth.esta_bloqueado(usuario):
        return "Bloqueado"
    if usuario["debe_cambiar_password"]:
        return "Debe cambiar clave"
    return "Activo"


def _mostrar_password_generada() -> None:
    """
    Muestra la contrasena provisoria una sola vez.

    Se muestra en pantalla a proposito: el administrador tiene que
    poder dictarsela por telefono a quien corresponda. No queda guardada
    en ningun lado (en la base va solo el hash) y el usuario esta
    obligado a cambiarla en su primer ingreso.
    """
    datos = st.session_state.get(_CLAVE_PROVISORIA)
    if not datos:
        return

    st.success(f"Usuario **{datos['usuario']}** listo.")
    st.markdown("**Contraseña provisoria:**")
    st.code(datos["password"], language=None)
    st.caption(
        "Anotala o dictásela ahora: no se vuelve a mostrar y nadie puede verla "
        "después, ni vos. En su primer ingreso el sistema lo obliga a cambiarla."
    )
    if st.button("Ya la anoté, ocultar", key="ocultar_password_generada"):
        st.session_state.pop(_CLAVE_PROVISORIA, None)
        st.rerun()
    st.divider()


def _tabla_de_usuarios(usuarios: list[dict]) -> None:
    filas = []
    for u in usuarios:
        filas.append(
            {
                "Usuario": u["usuario"],
                "Nombre": u["nombre"],
                "Rol": u["rol"],
                "Empresas": u["empresas"] or "todas",
                "Sucursales": u["sucursales"] or "todas",
                "Estado": _texto_estado(u),
                "Último acceso": (
                    u["ultimo_acceso"].strftime("%d/%m/%Y %H:%M")
                    if u["ultimo_acceso"]
                    else "nunca"
                ),
            }
        )
    st.dataframe(pd.DataFrame(filas), width="stretch", hide_index=True)


def _formulario_de_alta(sesion: dict, empresas_disponibles: dict) -> None:
    with st.expander("➕ Crear un usuario nuevo"):
        with st.form("form_crear_usuario", clear_on_submit=False):
            columna_1, columna_2 = st.columns(2)
            with columna_1:
                usuario = st.text_input(
                    "Nombre de acceso",
                    help="Sin espacios ni tildes. Por ejemplo: jriveros",
                )
                rol = st.selectbox(
                    "Rol",
                    options=list(auth.ROLES),
                    index=list(auth.ROLES).index("gerencia"),
                    format_func=lambda r: f"{r} · {auth.ROLES_DESCRIPCION[r]}",
                )
            with columna_2:
                nombre = st.text_input("Nombre y apellido")
                email = st.text_input("Email (opcional)")

            empresas = st.multiselect(
                "Empresas que puede consultar",
                options=list(empresas_disponibles),
                format_func=lambda clave: empresas_disponibles[clave],
                help="Si no elegís ninguna, va a ver todas las de esta instalación.",
            )
            sucursales = st.text_input(
                "Sucursales permitidas (opcional)",
                help=(
                    "Nombres exactos separados por coma. Dejalo vacío para que "
                    "vea todas."
                ),
            )

            enviado = st.form_submit_button(
                "Crear usuario", type="primary", width="stretch"
            )

        if enviado:
            password = auth.generar_password_provisoria()
            creado, error = auth.crear_usuario(
                usuario=usuario,
                nombre=nombre,
                rol=rol,
                password=password,
                email=email,
                empresas=",".join(empresas),
                sucursales=sucursales,
                hecho_por=sesion["usuario"],
            )
            if not creado:
                st.error(error)
            else:
                st.session_state[_CLAVE_PROVISORIA] = {
                    "usuario": usuario.strip().lower(),
                    "password": password,
                }
                st.rerun()


def _formulario_de_edicion(sesion: dict, usuarios: list[dict], empresas_disponibles: dict) -> None:
    st.markdown("#### Modificar un usuario")

    nombres = [u["usuario"] for u in usuarios]
    elegido = st.selectbox(
        "Usuario",
        options=nombres,
        format_func=lambda nombre: next(
            f"{u['usuario']} — {u['nombre']}" for u in usuarios if u["usuario"] == nombre
        ),
        key="panel_usuarios_elegido",
    )
    datos = next(u for u in usuarios if u["usuario"] == elegido)
    es_uno_mismo = datos["usuario"] == sesion["usuario"]

    if es_uno_mismo:
        st.caption(
            "Este es tu propio usuario. No vas a poder desactivarlo ni quitarte "
            "el rol de administrador."
        )
    if auth.esta_bloqueado(datos):
        st.warning(
            f"Bloqueado por intentos fallidos hasta las "
            f"{datos['bloqueado_hasta'].strftime('%H:%M')}."
        )

    # --- Datos y permisos ---
    with st.form("form_editar_usuario"):
        columna_1, columna_2 = st.columns(2)
        with columna_1:
            nombre = st.text_input("Nombre y apellido", value=datos["nombre"])
            rol = st.selectbox(
                "Rol",
                options=list(auth.ROLES),
                index=list(auth.ROLES).index(datos["rol"]),
                format_func=lambda r: f"{r} · {auth.ROLES_DESCRIPCION[r]}",
            )
        with columna_2:
            email = st.text_input("Email (opcional)", value=datos["email"] or "")
            sucursales = st.text_input(
                "Sucursales permitidas", value=datos["sucursales"] or ""
            )

        seleccion_actual = [
            clave
            for clave in empresas_disponibles
            if clave in [p.strip() for p in (datos["empresas"] or "").split(",") if p.strip()]
        ]
        empresas = st.multiselect(
            "Empresas que puede consultar",
            options=list(empresas_disponibles),
            default=seleccion_actual,
            format_func=lambda clave: empresas_disponibles[clave],
            help="Vacío significa todas las empresas de esta instalación.",
        )

        guardar = st.form_submit_button(
            "Guardar cambios", type="primary", width="stretch"
        )

    if guardar:
        actualizado, error = auth.actualizar_usuario(
            usuario=datos["usuario"],
            nombre=nombre,
            rol=rol,
            email=email,
            empresas=",".join(empresas),
            sucursales=sucursales,
            hecho_por=sesion["usuario"],
        )
        if actualizado:
            st.success("Cambios guardados.")
            st.rerun()
        else:
            st.error(error)

    # --- Acciones sueltas ---
    st.markdown("##### Acciones")
    columna_1, columna_2, columna_3 = st.columns(3)

    with columna_1:
        if st.button("Restablecer contraseña", width="stretch", key="btn_reset_pass"):
            password = auth.generar_password_provisoria()
            hecho, error = auth.restablecer_password(
                datos["usuario"], password, hecho_por=sesion["usuario"]
            )
            if hecho:
                st.session_state[_CLAVE_PROVISORIA] = {
                    "usuario": datos["usuario"],
                    "password": password,
                }
                st.rerun()
            else:
                st.error(error)

    with columna_2:
        etiqueta = "Activar" if not datos["activo"] else "Desactivar"
        if st.button(etiqueta, width="stretch", key="btn_estado"):
            hecho, error = auth.cambiar_estado(
                datos["usuario"], not datos["activo"], hecho_por=sesion["usuario"]
            )
            if hecho:
                st.success(f"Usuario {etiqueta.lower()}do.")
                st.rerun()
            else:
                st.error(error)

    with columna_3:
        if st.button(
            "Desbloquear",
            width="stretch",
            key="btn_desbloquear",
            disabled=not auth.esta_bloqueado(datos),
        ):
            hecho, error = auth.desbloquear(datos["usuario"], hecho_por=sesion["usuario"])
            if hecho:
                st.success("Desbloqueado.")
                st.rerun()
            else:
                st.error(error)

    st.caption(
        "Los usuarios no se borran, se desactivan: así se conserva el registro "
        "de quién consultó qué. Un usuario desactivado no puede ingresar."
    )


def _registro_de_accesos() -> None:
    with st.expander("🔎 Últimos accesos y cambios"):
        eventos = auth.accesos_recientes(40)
        if not eventos:
            st.caption("Todavía no hay eventos registrados.")
            return
        filas = [
            {
                "Fecha": evento["creado_el"].strftime("%d/%m/%Y %H:%M"),
                "Usuario": evento["usuario"],
                "Evento": evento["evento"],
                "Detalle": evento["detalle"] or "",
            }
            for evento in eventos
        ]
        st.dataframe(pd.DataFrame(filas), width="stretch", hide_index=True)


def render(sesion: dict, empresas_disponibles: dict) -> None:
    """
    Dibuja el panel completo.

    `sesion` son los datos del usuario logueado, y `empresas_disponibles`
    el diccionario clave -> nombre de las empresas de esta instalacion.
    """
    if not auth.puede(sesion, "administra_usuarios"):
        st.error("Tu perfil no tiene permiso para administrar usuarios.")
        return

    st.subheader("Usuarios del sistema")

    _mostrar_password_generada()

    usuarios = auth.listar_usuarios()
    if not usuarios:
        st.warning(
            "No se pudo leer la lista de usuarios. Revisá la conexión con la "
            "base de autenticación."
        )
        return

    _tabla_de_usuarios(usuarios)
    _formulario_de_alta(sesion, empresas_disponibles)
    st.divider()
    _formulario_de_edicion(sesion, usuarios, empresas_disponibles)
    st.divider()
    _registro_de_accesos()
