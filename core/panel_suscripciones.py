"""
core/panel_suscripciones.py
============================
Pantalla comercial de Conepasa. NO es para el cliente.

Solo la ve quien tiene la bandera `es_operador` en la base de usuarios.
Esa bandera no se puede otorgar desde ninguna pantalla: se pone a mano
con una consulta SQL. Es a proposito. El rol `admin` es el admin DEL
CLIENTE -administra los usuarios de su propia empresa- y no tiene por
que poder levantarse su propio corte de servicio. Si "operador" fuera un
rol comun, cualquier admin de cliente podria crearse un usuario con ese
rol desde la pantalla de usuarios y quedar fuera de alcance.

Toda la logica vive en core/suscripcion.py. Esta pantalla solo muestra y
pide confirmacion.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from core import suscripcion


_COLORES = {
    "ACTIVO": "🟢",
    "GRACIA": "🟡",
    "SUSPENDIDO": "🔴",
    "BAJA": "⚫",
}


def _formato_gs(valor) -> str:
    try:
        return "Gs. " + f"{int(valor):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "—"


def _fecha(valor) -> str:
    return valor.strftime("%d/%m/%Y") if hasattr(valor, "strftime") else "—"


def _resumen(clientes: list[dict]) -> None:
    conteo = {"ACTIVO": 0, "GRACIA": 0, "SUSPENDIDO": 0, "BAJA": 0}
    for cliente in clientes:
        clave = str(cliente.get("estado_efectivo", "")).upper()
        if clave in conteo:
            conteo[clave] += 1

    columnas = st.columns(4)
    for columna, (clave, etiqueta) in zip(
        columnas,
        [
            ("ACTIVO", "Al día"),
            ("GRACIA", "En gracia"),
            ("SUSPENDIDO", "Cortados"),
            ("BAJA", "De baja"),
        ],
    ):
        with columna:
            st.metric(f"{_COLORES[clave]} {etiqueta}", conteo[clave])


def _tabla(clientes: list[dict]) -> None:
    filas = []
    for cliente in clientes:
        estado_actual = str(cliente.get("estado_efectivo", "")).upper()
        interruptor = str(cliente.get("override_manual", "NINGUNO")).upper()
        filas.append(
            {
                "": _COLORES.get(estado_actual, "⚪"),
                "Cliente": cliente.get("razon_social") or cliente.get("cliente_id"),
                "ID": cliente.get("cliente_id"),
                "Plan": cliente.get("plan"),
                "Estado": estado_actual,
                "Pagado hasta": _fecha(cliente.get("paga_hasta")),
                "Días": cliente.get("dias_para_vencer"),
                "Corta el": _fecha(cliente.get("corta_el")),
                "Interruptor": "—" if interruptor == "NINGUNO" else interruptor,
            }
        )
    st.dataframe(pd.DataFrame(filas), width="stretch", hide_index=True)


def _alta(usuario: str) -> None:
    with st.expander("➕ Dar de alta un cliente nuevo"):
        with st.form("form_alta_cliente"):
            columna_1, columna_2 = st.columns(2)
            with columna_1:
                cliente_id = st.text_input(
                    "Identificador",
                    help=(
                        "Sin espacios ni tildes, en minúsculas. Es el mismo valor "
                        "que va en CLIENTE_ID del .env de esa instalación. "
                        "Por ejemplo: molinosur"
                    ),
                )
                plan = st.selectbox("Plan", ["BASE", "PREMIUM", "PILOTO", "INTERNO"])
                max_usuarios = st.number_input(
                    "Usuarios permitidos", min_value=1, max_value=99, value=3
                )
                dias_gracia = st.number_input(
                    "Días de gracia", min_value=0, max_value=60, value=3
                )
            with columna_2:
                razon_social = st.text_input("Razón social")
                ruc = st.text_input("RUC (opcional)")
                instalador = st.text_input("Instalador", value="")
                paga_hasta = st.date_input(
                    "Pagado hasta",
                    value=date.today() + timedelta(days=30),
                    format="DD/MM/YYYY",
                )

            st.caption("Contacto para cobranza (se muestra si el servicio se corta)")
            columna_3, columna_4, columna_5 = st.columns(3)
            with columna_3:
                contacto_nombre = st.text_input("Nombre")
            with columna_4:
                contacto_telefono = st.text_input("Teléfono")
            with columna_5:
                contacto_email = st.text_input("Email")

            enviado = st.form_submit_button(
                "Dar de alta", type="primary", width="stretch"
            )

        if enviado:
            creado, error = suscripcion.crear_cliente(
                cliente=cliente_id,
                razon_social=razon_social,
                plan=plan,
                max_usuarios=int(max_usuarios),
                paga_hasta=paga_hasta,
                dias_gracia=int(dias_gracia),
                ruc=ruc,
                contacto_nombre=contacto_nombre,
                contacto_email=contacto_email,
                contacto_telefono=contacto_telefono,
                instalador=instalador,
                hecho_por=usuario,
            )
            if creado:
                st.success(f"Cliente '{cliente_id.strip().lower()}' dado de alta.")
                st.rerun()
            else:
                st.error(error)


def _registrar_pago(cliente: dict, usuario: str) -> None:
    st.markdown("##### Registrar un pago")

    paga_hasta = cliente.get("paga_hasta")
    desde_sugerido = (
        paga_hasta + timedelta(days=1) if hasattr(paga_hasta, "year") else date.today()
    )

    with st.form(f"form_pago_{cliente['cliente_id']}"):
        columna_1, columna_2, columna_3 = st.columns(3)
        with columna_1:
            periodo_desde = st.date_input(
                "Período desde", value=desde_sugerido, format="DD/MM/YYYY"
            )
            monto = st.number_input(
                "Monto (Gs.)", min_value=0, step=50_000, value=850_000
            )
        with columna_2:
            periodo_hasta = st.date_input(
                "Período hasta",
                value=desde_sugerido + timedelta(days=29),
                format="DD/MM/YYYY",
            )
            medio = st.selectbox(
                "Medio", ["TRANSFERENCIA", "EFECTIVO", "CHEQUE", "BANCARD", "OTRO"]
            )
        with columna_3:
            fecha_pago = st.date_input(
                "Fecha del pago", value=date.today(), format="DD/MM/YYYY"
            )
            comprobante = st.text_input("Comprobante (opcional)")

        guardar = st.form_submit_button(
            "Registrar pago", type="primary", width="stretch"
        )

    if guardar:
        hecho, error = suscripcion.registrar_pago(
            cliente=cliente["cliente_id"],
            periodo_desde=periodo_desde,
            periodo_hasta=periodo_hasta,
            monto_gs=int(monto),
            medio=medio,
            fecha_pago=fecha_pago,
            comprobante=comprobante,
            hecho_por=usuario,
        )
        if hecho:
            st.success(
                f"Pago registrado. Cubierto hasta el {periodo_hasta.strftime('%d/%m/%Y')}."
            )
            st.rerun()
        else:
            st.error(error)

    st.caption(
        "Registrar un pago adelanta la fecha de corte y libera automáticamente "
        "un corte manual que estuviera puesto."
    )


def _interruptor(cliente: dict, usuario: str) -> None:
    st.markdown("##### Interruptor manual")

    actual = str(cliente.get("override_manual", "NINGUNO")).upper()
    if actual != "NINGUNO":
        st.info(
            f"Interruptor puesto en **{actual}**. "
            f"Motivo: {cliente.get('override_motivo') or 'sin motivo registrado'}."
        )
        st.caption(
            "Mientras esté puesto, la fecha de pago no decide nada para este cliente."
        )
    else:
        st.caption(
            "Sin interruptor: manda la fecha de pago. Es lo normal y es como "
            "debería quedar la mayoría del tiempo."
        )

    with st.form(f"form_override_{cliente['cliente_id']}"):
        opcion = st.radio(
            "Qué hacer",
            options=["NINGUNO", "FORZAR_ACTIVO", "FORZAR_SUSPENDIDO"],
            format_func=lambda valor: {
                "NINGUNO": "Sin interruptor — que decida la fecha de pago",
                "FORZAR_ACTIVO": "Dejarlo entrar aunque esté vencido",
                "FORZAR_SUSPENDIDO": "Cortar el servicio ahora",
            }[valor],
            index=["NINGUNO", "FORZAR_ACTIVO", "FORZAR_SUSPENDIDO"].index(actual),
        )
        motivo = st.text_input(
            "Motivo",
            value=cliente.get("override_motivo") or "",
            help="Obligatorio salvo que saques el interruptor. Queda en el registro.",
        )
        usar_vencimiento = st.checkbox(
            "Que el interruptor se apague solo en una fecha", value=False
        )
        vence = st.date_input(
            "Hasta", value=date.today() + timedelta(days=7), format="DD/MM/YYYY"
        )

        aplicar = st.form_submit_button("Aplicar", type="primary", width="stretch")

    if aplicar:
        hecho, error = suscripcion.cambiar_override(
            cliente=cliente["cliente_id"],
            override=opcion,
            motivo=motivo,
            vence=vence if (usar_vencimiento and opcion != "NINGUNO") else None,
            hecho_por=usuario,
        )
        if hecho:
            st.success("Interruptor actualizado.")
            st.rerun()
        else:
            st.error(error)


def _ajustes(cliente: dict, usuario: str) -> None:
    with st.expander("⚙️ Mensaje de corte, días de gracia y baja"):
        with st.form(f"form_ajustes_{cliente['cliente_id']}"):
            mensaje = st.text_area(
                "Mensaje que ve el cliente si el servicio está cortado",
                value=cliente.get("mensaje_cliente") or "",
                help=(
                    "Dejalo vacío para usar el texto por defecto. Lo lee un "
                    "gerente, no quien decide los pagos: conviene que sea "
                    "comercial y no un reproche."
                ),
                height=80,
            )
            dias_gracia = st.number_input(
                "Días de gracia",
                min_value=0,
                max_value=60,
                value=int(cliente.get("dias_gracia") or 3),
            )
            guardar = st.form_submit_button("Guardar", width="stretch")

        if guardar:
            ok_1, error_1 = suscripcion.cambiar_mensaje(
                cliente["cliente_id"], mensaje, hecho_por=usuario
            )
            ok_2, error_2 = suscripcion.cambiar_dias_gracia(
                cliente["cliente_id"], int(dias_gracia), hecho_por=usuario
            )
            if ok_1 and ok_2:
                st.success("Guardado.")
                st.rerun()
            else:
                st.error(error_1 or error_2)

        st.divider()

        if str(cliente.get("estado_efectivo", "")).upper() == "BAJA":
            motivo_alta = st.text_input(
                "Motivo de la reactivación", key=f"alta_{cliente['cliente_id']}"
            )
            if st.button("Reactivar cliente", width="stretch"):
                hecho, error = suscripcion.reactivar_cliente(
                    cliente["cliente_id"], motivo_alta, hecho_por=usuario
                )
                if hecho:
                    st.success("Cliente reactivado.")
                    st.rerun()
                else:
                    st.error(error)
        else:
            motivo_baja = st.text_input(
                "Motivo de la baja", key=f"baja_{cliente['cliente_id']}"
            )
            confirmar = st.checkbox(
                "Confirmo que este cliente termina el servicio",
                key=f"confirma_baja_{cliente['cliente_id']}",
            )
            if st.button("Dar de baja", width="stretch", disabled=not confirmar):
                hecho, error = suscripcion.dar_de_baja(
                    cliente["cliente_id"], motivo_baja, hecho_por=usuario
                )
                if hecho:
                    st.success("Cliente dado de baja.")
                    st.rerun()
                else:
                    st.error(error)
            st.caption(
                "La baja no borra nada: apaga el acceso y conserva el historial "
                "completo. Se puede revertir."
            )


def _historial(cliente: dict) -> None:
    columna_1, columna_2 = st.columns(2)

    with columna_1:
        st.markdown("##### Pagos")
        pagos = suscripcion.pagos_recientes(cliente["cliente_id"], 12)
        if not pagos:
            st.caption("Todavía no hay pagos registrados.")
        else:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Fecha": _fecha(pago["fecha_pago"]),
                            "Cubre hasta": _fecha(pago["periodo_hasta"]),
                            "Monto": _formato_gs(pago["monto_gs"]),
                            "Medio": pago["medio"],
                        }
                        for pago in pagos
                    ]
                ),
                width="stretch",
                hide_index=True,
            )

    with columna_2:
        st.markdown("##### Movimientos")
        eventos = suscripcion.eventos_recientes(cliente["cliente_id"], 12)
        if not eventos:
            st.caption("Sin movimientos registrados.")
        else:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Fecha": evento["ocurrido_en"].strftime("%d/%m/%Y %H:%M"),
                            "Evento": evento["evento"],
                            "Detalle": evento["detalle"] or "",
                            "Quién": evento["usuario"] or "",
                        }
                        for evento in eventos
                    ]
                ),
                width="stretch",
                hide_index=True,
            )


def render(sesion: dict) -> None:
    """Dibuja el panel completo. `sesion` son los datos del usuario logueado."""
    if not sesion.get("es_operador"):
        st.error("Esta pantalla es de uso interno de Conepasa.")
        return

    usuario = sesion.get("usuario", "")

    st.subheader("Suscripciones")
    st.caption(
        "Pantalla interna de Conepasa. El cliente nunca ve esto, ni siquiera "
        "el administrador de su propia empresa."
    )

    clientes = suscripcion.listar_clientes()
    if not clientes:
        st.warning(
            "No se pudo leer la lista de clientes, o todavía no hay ninguno "
            "cargado. Revisá que exista la vista `v_estado_suscripcion` en la "
            "base de autenticación."
        )
        _alta(usuario)
        return

    _resumen(clientes)
    st.divider()
    _tabla(clientes)
    _alta(usuario)
    st.divider()

    st.markdown("#### Administrar un cliente")
    identificadores = [cliente["cliente_id"] for cliente in clientes]
    elegido = st.selectbox(
        "Cliente",
        options=identificadores,
        format_func=lambda valor: next(
            f"{_COLORES.get(str(c['estado_efectivo']).upper(), '⚪')} "
            f"{c['razon_social']} ({c['cliente_id']})"
            for c in clientes
            if c["cliente_id"] == valor
        ),
        key="panel_susc_elegido",
    )
    cliente = next(c for c in clientes if c["cliente_id"] == elegido)

    estado_actual = str(cliente.get("estado_efectivo", "")).upper()
    columna_1, columna_2, columna_3 = st.columns(3)
    columna_1.metric("Estado", f"{_COLORES.get(estado_actual, '⚪')} {estado_actual}")
    columna_2.metric("Pagado hasta", _fecha(cliente.get("paga_hasta")))
    columna_3.metric("Corta el", _fecha(cliente.get("corta_el")))

    _registrar_pago(cliente, usuario)
    st.divider()
    _interruptor(cliente, usuario)
    _ajustes(cliente, usuario)
    st.divider()
    _historial(cliente)
