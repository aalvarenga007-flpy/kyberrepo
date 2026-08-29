"""Panel Streamlit de monitoreo y sincronizacion manual para administradores."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from core.sync_monitor import SyncMonitorError, obtener_estado, sincronizar_ahora


@st.cache_data(ttl=30, show_spinner=False)
def _estado_cacheado(company: str) -> dict:
    return obtener_estado(company)


def _fecha(valor) -> str:
    if not valor:
        return "Nunca"
    return str(valor).replace("T", " ")[:19]


def _estado_visible(vista: dict) -> str:
    cola = vista.get("queue") or {}
    estado_cola = cola.get("status")
    if estado_cola == "running":
        return "Ejecutando"
    if estado_cola == "pending":
        return "En cola"
    if estado_cola == "paused":
        return "Pausada"
    return {
        "ok": "Al día",
        "error": "Con error",
        "running": "Ejecutando",
        "never": "Nunca",
    }.get(str(vista.get("last_sync_status") or "never"), "Desconocido")


def _minutos_desde(valor, server_time) -> int | None:
    try:
        fin = datetime.fromisoformat(str(valor).replace(" ", "T"))
        ahora = datetime.fromisoformat(str(server_time).replace(" ", "T"))
        return max(0, int((ahora - fin).total_seconds() // 60))
    except (TypeError, ValueError):
        return None


def render(company: str, company_name: str) -> None:
    st.subheader("🔄 Actualización de datos")
    st.caption(
        f"Consultá cuándo se actualizaron por última vez los datos de {company_name} "
        "y, cuando esté habilitado, pedí una actualización sin salir de Kyber."
    )

    if st.button("Volver a consultar el estado", key=f"sync_refresh_{company}"):
        _estado_cacheado.clear()
        st.rerun()

    try:
        payload = _estado_cacheado(company)
    except SyncMonitorError as exc:
        st.info(
            "Los datos continúan actualizándose automáticamente cada hora. "
            "Podés seguir usando Conepasa IA normalmente."
        )
        st.warning(
            "La actualización manual todavía no está habilitada en esta pantalla de pruebas."
        )
        st.markdown(
            "**Esto no significa que haya un error en tus datos.** "
            "Por ahora, si necesitás traer información nueva de inmediato, avisale a Adrián. "
            "Cuando habilitemos el botón, vas a poder hacerlo directamente desde acá."
        )
        st.button(
            "Actualizar datos ahora (próximamente)",
            disabled=True,
            width="stretch",
            key=f"sync_unavailable_{company}",
        )
        with st.expander("Información para soporte"):
            st.caption(str(exc))
        return

    vistas = list(payload.get("views") or [])
    ventas = next((v for v in vistas if v.get("name") == "Ventas"), {})
    en_cola = sum(
        1
        for vista in vistas
        if (vista.get("queue") or {}).get("status") in {"pending", "running", "paused"}
    )
    minutos = _minutos_desde(ventas.get("last_sync_end"), payload.get("server_time"))
    antiguedad = "Nunca" if minutos is None else (f"{minutos} min" if minutos < 120 else f"{minutos // 60} h")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Última actualización de Ventas", _fecha(ventas.get("last_sync_end")))
    col2.metric("Tiempo transcurrido", antiguedad)
    col3.metric("Tareas en curso", en_cola)
    col4.metric("Actualización automática", f"Cada {payload.get('auto_sync_hours', '?')} h")
    with st.expander("Información técnica para soporte"):
        st.caption(
            f"Última actividad del proceso: {_fecha(payload.get('worker_last_run'))} · "
            f"Período que se vuelve a revisar: {payload.get('resync_days', '?')} días"
        )

    frecuencia = int(payload.get("auto_sync_hours") or 0)
    if frecuencia > 1:
        st.warning(
            f"La actualización automática está configurada para ejecutarse cada "
            f"{frecuencia} horas."
        )
    elif minutos is not None and minutos > 120:
        st.warning("Ventas lleva más de dos horas sin una actualización exitosa.")

    filas = []
    for vista in vistas:
        cola = vista.get("queue") or {}
        filas.append(
            {
                "Datos": vista.get("name"),
                "Estado": _estado_visible(vista),
                "Última actualización": _fecha(vista.get("last_sync_end")),
                "Registros": int(vista.get("total_records_local") or 0),
                "Nuevos": int(vista.get("last_records_inserted") or 0),
                "Actualizados": int(vista.get("last_records_updated") or 0),
                "Progreso": int(cola.get("records_downloaded") or 0),
            }
        )
    st.dataframe(pd.DataFrame(filas), width="stretch", hide_index=True)

    with st.expander("Últimas ejecuciones de Ventas"):
        historial = payload.get("sales_history") or []
        tabla_historial = [
            {
                "Inicio": _fecha(item.get("started_at")),
                "Fin": _fecha(item.get("finished_at")),
                "Estado": item.get("status"),
                "Insertados": int(item.get("records_inserted") or 0),
                "Actualizados": int(item.get("records_updated") or 0),
                "Duración (s)": round(float(item.get("elapsed_seconds") or 0), 1),
            }
            for item in historial
        ]
        st.dataframe(pd.DataFrame(tabla_historial), width="stretch", hide_index=True)

    st.markdown("#### Actualizar ahora")
    st.caption(
        "Usá esta opción cuando necesites consultar datos que se cargaron después "
        "de la última actualización automática."
    )
    nombres = [str(v.get("name")) for v in vistas if v.get("name")]
    if not nombres:
        st.info("No hay vistas configuradas para sincronizar.")
        return
    vista = st.selectbox("Vista", nombres, index=nombres.index("Ventas") if "Ventas" in nombres else 0)
    confirmar = st.checkbox(
        "Confirmo que deseo traer los datos más recientes ahora",
        key=f"sync_confirm_{company}",
    )
    col_vista, col_todo = st.columns(2)
    ejecutar_vista = col_vista.button(
        f"Actualizar solo {vista}",
        disabled=not confirmar,
        width="stretch",
        key=f"sync_one_{company}",
    )
    ejecutar_todo = col_todo.button(
        "Actualizar todos los datos",
        disabled=not confirmar,
        width="stretch",
        key=f"sync_all_{company}",
    )
    if ejecutar_vista or ejecutar_todo:
        try:
            resultado = sincronizar_ahora(company, vista if ejecutar_vista else None)
        except SyncMonitorError as exc:
            st.error(str(exc))
        else:
            _estado_cacheado.clear()
            cantidad = int(resultado.get("queued") or 0)
            st.success(f"Actualización solicitada: {cantidad} tarea(s) preparada(s).")
            st.info("En unos instantes el estado cambiará a Ejecutando.")
