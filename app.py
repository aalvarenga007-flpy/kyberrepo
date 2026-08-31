from datetime import date
from html import escape
from io import BytesIO

import pandas as pd
import streamlit as st

from ai.agent import DataAnalystAgent
from core import (
    auth,
    estilos,
    panel_sincronizacion,
    panel_suscripciones,
    panel_usuarios,
    suscripcion,
)
from core.audit import recent_queries, write_log
from core.config import settings
from core.consumo import estado_cupo, puede_consultar
from core.db import ping
from core.formato import formatear_dataframe_para_mostrar, formatear_numero
from core.lfl import DESPLAZAMIENTO_DIAS, empresas_disponibles, lfl_comparison, list_branches
from core.version import APP_VERSION
from core.panel_access import crear_enlace

# El modulo de presupuestos es opcional: si el archivo no esta o le falta una
# dependencia, la app sigue funcionando igual y la pestana simplemente no
# aparece. Nunca debe tumbar el chat ni el LFL, que son lo critico.
try:
    import presupuestos_ui
except Exception as _error_presupuestos:  # noqa: BLE001
    presupuestos_ui = None
    _MOTIVO_SIN_PRESUPUESTOS = str(_error_presupuestos)
else:
    _MOTIVO_SIN_PRESUPUESTOS = ""

st.set_page_config(page_title=settings.app_name, page_icon="🤖", layout="wide")

# Ajustes visuales para pantalla chica. Va antes que todo lo demás para
# que también se apliquen a la pantalla de login. Ver core/estilos.py.
estilos.aplicar_estilos()

# ---------------------------------------------------------------------
# COMPUERTA DE ACCESO (Fase 0.2)
#
# Tiene que estar aca arriba, antes de dibujar cualquier cosa y antes de
# tocar las bases del negocio. Si no hay sesion valida, exigir_login()
# dibuja la pantalla de ingreso y corta la ejecucion del script.
# Nada de lo que sigue se llega a ejecutar.
# ---------------------------------------------------------------------
usuario_sesion = auth.exigir_login()

st.title("🤖 Conepasa IA")

# Se dibuja con markdown y no con st.caption para poder marcarlo con una
# clase propia: en pantalla chica se oculta (ver core/estilos.py). Es un
# texto de presentación, no información operativa, y en un celular se
# come la primera pantalla cada vez que se abre la app.
st.markdown(
    '<p class="cnp-subtitulo">'
    "Consultá la información de tu empresa en lenguaje natural. "
    "Cada cifra sale de una consulta real a la base de datos."
    "</p>",
    unsafe_allow_html=True,
)

# Aviso de vencimiento próximo o de período de gracia. Devuelve cadena
# vacía en la situación normal, que es la mayor parte del tiempo: si esto
# apareciera todos los días dejaría de leerse a los tres días.
_aviso_suscripcion = suscripcion.aviso_en_pantalla(
    st.session_state.get("conepasa_estado_suscripcion")
)
if _aviso_suscripcion:
    st.warning(_aviso_suscripcion)

if not settings.anthropic_api_key:
    st.error(
        "Falta configurar ANTHROPIC_API_KEY en claude_engine/.env. "
        "Copiá .env.example a .env y completá la key antes de continuar."
    )
    st.stop()

TODAS_LAS_COMPANIES = {
    "ekaru": ("Ekarú Gastronomía", settings.ekaru_database),
    "ejapo": ("Ejapo Comercial San José", settings.ejapo_database),
}

# El usuario solo ve las empresas que tiene asignadas. Si el campo
# `empresas` esta vacio en la base, ve todas.
COMPANIES = auth.filtrar_empresas(usuario_sesion, TODAS_LAS_COMPANIES)

if not COMPANIES:
    st.error(
        "Tu usuario no tiene ninguna empresa asignada. "
        "Pedile al administrador que revise tu configuración."
    )
    st.stop()


def tipo_de_bloque(bloque) -> str:
    """
    Tipo de un bloque de contenido de la respuesta ("text", "tool_use"...).

    Se lee de las dos formas posibles a proposito. Los bloques que
    devuelve la API son objetos, pero los que se rearman desde un
    historial guardado son diccionarios. Leer solo con getattr hacia que
    un diccionario devolviera siempre None, y ahi todo mensaje pasaba
    por respuesta final.
    """
    if isinstance(bloque, dict):
        return str(bloque.get("type") or "")
    return str(getattr(bloque, "type", "") or "")


def texto_de_bloque(bloque) -> str:
    """Texto de un bloque, con la misma tolerancia que tipo_de_bloque."""
    if isinstance(bloque, dict):
        return str(bloque.get("text") or "")
    return str(getattr(bloque, "text", "") or "")


def build_excel_bytes(dataframe: pd.DataFrame) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        dataframe.to_excel(writer, sheet_name="Resultado", index=False)
        worksheet = writer.sheets["Resultado"]
        for cells in worksheet.columns:
            maximum = max(len(str(cell.value or "")) for cell in cells)
            worksheet.column_dimensions[cells[0].column_letter].width = min(maximum + 3, 45)
    output.seek(0)
    return output.getvalue()


def render_downloads(executed_sql: list[dict], turn_key: str) -> None:
    """Muestra la tabla y los botones de descarga de cada consulta SQL de un turno."""
    downloadable = [item for item in executed_sql if item.get("dataframe") is not None and not item["dataframe"].empty]
    if not downloadable:
        # Red de seguridad. Que este bloque desaparezca sin decir nada fue
        # exactamente el sintoma que costo diagnosticar: consultas que
        # corrian bien y descargas que no aparecian. Si hubo SQL ejecutado
        # pero no quedo ninguna tabla para exportar, en modo diagnostico se
        # deja constancia en vez de irse en silencio.
        # `debug_mode` se define mas abajo, al dibujar la barra lateral.
        # Python lo resuelve al llamar la funcion, no al definirla, y para
        # entonces ya existe.
        if executed_sql and globals().get("debug_mode"):
            st.caption(
                f"Sin tabla para exportar: {len(executed_sql)} consulta(s) ejecutada(s), "
                "ninguna devolvio filas."
            )
        return

    with st.expander(f"📥 Descargar resultados ({len(downloadable)} tabla(s))"):
        for index, item in enumerate(downloadable, start=1):
            dataframe = item["dataframe"]
            st.caption(f"Consulta {index} · {len(dataframe)} fila(s)")
            st.dataframe(formatear_dataframe_para_mostrar(dataframe), use_container_width=True, hide_index=True)

            col_csv, col_xlsx = st.columns(2)
            csv_bytes = dataframe.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")
            col_csv.download_button(
                "Descargar CSV",
                data=csv_bytes,
                file_name=f"conepasa_ia_{turn_key}_{index}.csv",
                mime="text/csv",
                use_container_width=True,
                key=f"csv_{turn_key}_{index}",
            )
            col_xlsx.download_button(
                "Descargar Excel",
                data=build_excel_bytes(dataframe),
                file_name=f"conepasa_ia_{turn_key}_{index}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key=f"xlsx_{turn_key}_{index}",
            )

            if index < len(downloadable):
                st.divider()


with st.sidebar:
    auth.bloque_usuario_en_sidebar(usuario_sesion)
    st.divider()

    st.subheader("Empresa activa")
    if len(COMPANIES) == 1:
        # Con una sola empresa asignada el selector no aporta nada y
        # ademas confunde: se muestra el nombre y listo.
        company = next(iter(COMPANIES))
        st.caption(COMPANIES[company][0])
    else:
        company = st.radio(
            "Empresa",
            options=list(COMPANIES),
            format_func=lambda key: COMPANIES[key][0],
            label_visibility="collapsed",
        )
    database = COMPANIES[company][1]

    connected, message = ping(database)
    if connected:
        st.success(f"Conectado a {database}")
    else:
        st.error("Sin conexión a la base de datos")
        st.caption(message)

    # --- Consumo del plan --------------------------------------------
    # Se muestra a TODOS los roles, no solo al administrador: la pregunta
    # "cuantas consultas me quedan" es la que mas repiten los usuarios, y
    # tener que pedirsela a otro es exactamente el problema que resuelve.
    #
    # El cupo es de la EMPRESA ACTIVA. Un usuario con acceso a dos empresas
    # ve numeros distintos segun donde este parado, y eso es correcto: son
    # dos planes contratados por separado.
    cupo = estado_cupo(company, usuario_sesion["usuario"])

    st.divider()
    st.subheader("Consultas del plan")

    if cupo.nivel == "sin_plan":
        # No se bloquea a nadie por esto (ver core/consumo.py), pero tampoco
        # se esconde: es un error de configuracion que hay que resolver.
        st.caption("⚙️ Sin plan configurado")
    elif cupo.ilimitado:
        st.caption(cupo.texto_badge)
    else:
        referencia = (
            cupo.usuario_usadas / cupo.tope_usuario
            if cupo.tiene_tope_propio
            else cupo.porcentaje
        )
        st.progress(min(1.0, max(0.0, referencia)))
        st.caption(cupo.texto_badge)

        if cupo.nivel == "bloqueado":
            st.error("Cupo agotado")
        elif cupo.nivel == "critico":
            st.warning(f"Quedan {cupo.restantes_usuario} consultas")
        elif cupo.nivel == "aviso":
            st.info(f"Quedan {cupo.restantes_usuario} consultas")

    # El SQL crudo y el historial de auditoría exponen nombres de tablas y
    # preguntas hechas por otros usuarios. Solo para perfiles técnicos.
    ve_detalle = auth.puede(usuario_sesion, "ve_detalle_tecnico")
    if ve_detalle:
        debug_mode = st.toggle("Modo técnico (ver SQL ejecutado)", value=True)
    else:
        debug_mode = False

    st.divider()
    if st.button("Nueva conversación", use_container_width=True):
        st.session_state.pop(f"messages_{company}", None)
        st.session_state.pop(f"debug_{company}", None)
        st.rerun()

    # Los perfiles técnicos ven el historial completo de la empresa; el
    # resto ve únicamente sus propias consultas.
    with st.expander("Historial de auditoría"):
        history = recent_queries(
            company,
            limit=15,
            usuario=None if ve_detalle else usuario_sesion["usuario"],
        )
        if not history:
            st.caption("Todavía no hay consultas registradas.")
        for created_at, question, rows, error, quien in history:
            status = "⚠️" if error else "✅"
            autor = quien or "sistema"
            st.caption(
                f"{status} {created_at.replace('T', ' ')} · {autor} · {rows} fila(s)"
                f"\n\n{question}"
            )

    # Identificador funcional del despliegue. La clase lo fija en la esquina
    # inferior izquierda del panel para que siempre sea visible sin ocupar
    # espacio entre los controles de trabajo.
    panel_link = ""
    if auth.puede(usuario_sesion, "administra_sincronizacion"):
        try:
            panel_url = crear_enlace(usuario_sesion, company, st.session_state,
                                     st.session_state[auth.CLAVE_EXPIRA].timestamp())
            panel_link = (
                f'<a class="kyber-panel-link" href="{escape(panel_url, quote=True)}" '
                'target="_blank" rel="noopener noreferrer">🔄 Panel de sincronización</a>'
            )
        except (ValueError, OSError, PermissionError):
            panel_url = None
    st.markdown(
        f'<div class="kyber-sidebar-footer">{panel_link}'
        f'<div class="kyber-sidebar-version">v{APP_VERSION}</div></div>',
        unsafe_allow_html=True,
    )

# Empresas que cotizan. Ekaru no presupuesta: es gastronomia.
EMPRESAS_CON_PRESUPUESTOS = ("ejapo",)


def _ve_presupuestos() -> bool:
    """
    La pestana de presupuestos se muestra si se cumplen las dos:
      1. el modulo esta encendido en el .env (MODULO_PRESUPUESTOS=true)
      2. el usuario tiene permiso

    OJO: la empresa activa NO entra en esta decision, aunque parezca lo
    natural. Si entrara, la cantidad de pestanas cambiaria de 4 a 5 al
    mover el selector de empresa, y Streamlit no reconcilia bien el
    contenido cuando el juego de pestanas cambia en medio de una sesion:
    las ultimas dos quedan dibujadas pero vacias. Es un problema conocido
    de Streamlit, no del codigo.

    Por eso la pestana esta siempre y el control por empresa se hace
    ADENTRO, mostrando un aviso cuando la empresa activa no cotiza. El
    codigo sigue siendo identico para las dos empresas.
    """
    if presupuestos_ui is None:
        return False
    if not presupuestos_ui.modulo_activo():
        return False

    try:
        if auth.puede(usuario_sesion, "usa_presupuestos"):
            return True
    except Exception:  # noqa: BLE001
        pass
    # Mientras auth no conozca el permiso "usa_presupuestos", lo ven el
    # administrador y el rol "presupuestos" que ya existe en la base.
    return (
        auth.puede(usuario_sesion, "administra_usuarios")
        or str(usuario_sesion.get("rol", "")).strip().lower() == "presupuestos"
    )


hay_presupuestos = _ve_presupuestos()

# Roles que no tienen habilitado ni el asistente ni el análisis LFL no
# tienen nada que hacer en esta pantalla, salvo que tengan presupuestos.
if (
    not auth.puede(usuario_sesion, "usa_chat")
    and not auth.puede(usuario_sesion, "usa_lfl")
    and not hay_presupuestos
):
    st.info(
        "Tu perfil no tiene habilitado el asistente ni el análisis LFL. "
        "Si esperabas ver otra cosa acá, consultá con el administrador del sistema."
    )
    st.stop()

# Se define ACÁ, fuera de las pestañas: st.chat_input solo queda fijo al
# fondo de la página cuando está en el nivel superior del script. Si se lo
# llama dentro de "with tab_chat:" (un contenedor), Streamlit lo dibuja
# como un widget más en el lugar del código, sin fijarlo a ningún lado.
# Solo para quien tiene el chat habilitado: sin esto, un usuario del rol
# "presupuestos" veria la barra de preguntas y podria consultar toda la base.
if auth.puede(usuario_sesion, "usa_chat"):
    question = st.chat_input(f"Preguntá lo que quieras sobre {COMPANIES[company][0]}...")
else:
    question = None

# La pestaña de usuarios solo existe para el rol admin. No se dibuja y
# se oculta; directamente no está.
puede_administrar = auth.puede(usuario_sesion, "administra_usuarios")
puede_administrar_sync = auth.puede(usuario_sesion, "administra_sincronizacion")

# La pestana de suscripciones es de uso interno de Conepasa. No la ve el
# cliente, ni siquiera el administrador de su propia empresa: depende de
# la bandera es_operador, que no se puede otorgar desde ninguna pantalla.
es_operador = bool(usuario_sesion.get("es_operador"))

etiquetas = ["💬 Asistente", "📊 Análisis LFL"]
if hay_presupuestos:
    etiquetas.append("🧾 Presupuestos")
if puede_administrar:
    etiquetas.append("👥 Usuarios")
if puede_administrar_sync:
    etiquetas.append("🔄 Actualizar datos")
if es_operador:
    etiquetas.append("💳 Suscripciones")

pestanas = st.tabs(etiquetas)
tab_chat, tab_lfl = pestanas[0], pestanas[1]

siguiente = 2
tab_presupuestos = None
if hay_presupuestos:
    tab_presupuestos = pestanas[siguiente]
    siguiente += 1
tab_usuarios = None
if puede_administrar:
    tab_usuarios = pestanas[siguiente]
    siguiente += 1
tab_sincronizacion = None
if puede_administrar_sync:
    tab_sincronizacion = pestanas[siguiente]
    siguiente += 1
tab_suscripciones = pestanas[siguiente] if es_operador else None

with tab_chat:
    session_key = f"messages_{company}"
    debug_key = f"debug_{company}"
    if session_key not in st.session_state:
        st.session_state[session_key] = []
    if debug_key not in st.session_state:
        st.session_state[debug_key] = []  # una lista de executed_sql, una por cada respuesta visible

    # ------------------------------------------------------------------
    # RECORRIDO DEL HISTORIAL
    #
    # El historial que devuelve el agente NO es "una respuesta por
    # pregunta": trae tambien todos los pasos intermedios. En cada vuelta
    # el modelo manda un mensaje que puede llevar texto ("voy a revisar
    # las ventas de la sucursal...") JUNTO con el pedido de herramienta.
    # Eso es un mensaje de assistant con texto, pero no es la respuesta
    # al usuario.
    #
    # Contarlos como respuestas desalineaba el indice contra debug_key,
    # que guarda UNA entrada por pregunta. Con eso, el bloque de
    # descargas caia en el mensaje equivocado o se perdia del todo: el
    # `if` de mas abajo devolvia una lista vacia y render_downloads se
    # iba en silencio, sin error ni aviso.
    #
    # La marca que distingue a la respuesta final es una sola y no falla:
    # NO pide ninguna herramienta. Cuando el modelo deja de pedir
    # consultas, termino. Por eso se cuenta por ahi y no por texto.
    # ------------------------------------------------------------------
    visible_turn_index = 0
    for message in st.session_state[session_key]:
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            with st.chat_message("user"):
                st.write(message["content"])
            continue

        if message.get("role") != "assistant":
            continue

        bloques = message.get("content") or []
        if isinstance(bloques, str):
            bloques = []

        # Paso intermedio del agente: no se dibuja. En vivo el usuario
        # tampoco lo vio, solo vio la respuesta final.
        if any(tipo_de_bloque(bloque) == "tool_use" for bloque in bloques):
            continue

        text = "".join(
            texto_de_bloque(bloque)
            for bloque in bloques
            if tipo_de_bloque(bloque) == "text"
        ).strip()
        if not text:
            continue

        with st.chat_message("assistant"):
            st.write(text)
            turn_executed_sql = (
                st.session_state[debug_key][visible_turn_index]
                if visible_turn_index < len(st.session_state[debug_key])
                else []
            )
            if debug_mode and turn_executed_sql:
                with st.expander(f"SQL ejecutado ({len(turn_executed_sql)} consulta(s))"):
                    for item in turn_executed_sql:
                        st.code(item["sql"], language="sql")
                        st.caption(f"{item['rows']} fila(s) devueltas.")
            render_downloads(turn_executed_sql, turn_key=f"{company}_{visible_turn_index}")
        visible_turn_index += 1

    if question:
        # Control de cupo ANTES de gastar una llamada a la API. La pregunta
        # no se guarda en el historial: el usuario no la consumio, asi que
        # cuando amplien el plan la puede volver a hacer tal cual.
        permitido, motivo_bloqueo = puede_consultar(company, usuario_sesion["usuario"])
        if not permitido:
            with st.chat_message("user"):
                st.write(question)
            with st.chat_message("assistant"):
                st.warning(motivo_bloqueo)
            st.stop()

        with st.chat_message("user"):
            st.write(question)

        st.session_state[session_key].append({"role": "user", "content": question})

        with st.chat_message("assistant"):
            with st.spinner("Analizando tu base de datos..."):
                try:
                    agent = DataAnalystAgent(
                        company=company,
                        database=database,
                        rol=usuario_sesion["rol"],
                        usuario=usuario_sesion["usuario"],
                    )
                    response = agent.ask(st.session_state[session_key])
                except Exception as error:
                    write_log("ERROR", f"[{company}] Fallo del agente: {error}")
                    st.error("No se pudo completar el análisis.")
                    if debug_mode:
                        st.exception(error)
                    st.stop()

            # ----------------------------------------------------------
            # GUARDAR ANTES DE DIBUJAR.
            #
            # Estas dos lineas van pegadas al retorno del agente y no al
            # final del bloque. Entre medio hay cuatro llamadas a Streamlit
            # (write, expander, code y sobre todo render_downloads) y
            # cualquiera de ellas puede cortar la corrida sin dejar rastro:
            # una excepcion adentro de render_downloads, o un rerun
            # pendiente que Streamlit atiende en el siguiente st.* y aborta
            # el script en silencio (runner.fastReruns).
            #
            # Con el guardado al final, ese corte dejaba el peor estado
            # posible: la pregunta del usuario YA estaba en el historial
            # (el append de mas arriba muta la lista en el lugar) pero la
            # respuesta no. En pantalla desaparecia la respuesta recien
            # recibida, y en el turno siguiente el modelo recibia dos
            # mensajes de usuario seguidos y volvia a contestar la pregunta
            # anterior.
            #
            # El indice del turno se calcula ANTES del append para que la
            # clave de los botones de descarga siga siendo la misma que le
            # asigna el recorrido del historial de mas arriba.
            # ----------------------------------------------------------
            indice_del_turno = len(st.session_state[debug_key])
            st.session_state[session_key] = response["messages"]
            st.session_state[debug_key].append(response["executed_sql"])

            st.write(response["text"])

            if debug_mode and response["executed_sql"]:
                with st.expander(f"SQL ejecutado ({len(response['executed_sql'])} consulta(s))"):
                    for item in response["executed_sql"]:
                        st.code(item["sql"], language="sql")
                        st.caption(f"{item['rows']} fila(s) devueltas.")

            render_downloads(response["executed_sql"], turn_key=f"{company}_{indice_del_turno}")

        # El sidebar (donde vive el contador de consultas) se dibuja al
        # principio del script, o sea ANTES de que el agente responda y
        # registre el consumo. Sin esto, el numero que ve el usuario queda
        # siempre una consulta atrasado hasta la interaccion siguiente.
        #
        # La respuesta y el SQL del turno ya quedaron guardados en
        # session_state apenas volvio el agente, asi que al redibujarse la
        # pantalla se muestran igual desde el historial.
        st.rerun()

with tab_lfl:
    st.subheader("Análisis LFL (Like-for-Like) por día de la semana")
    st.caption(
        f"Compara un período contra el mismo período 52 semanas atrás "
        f"({DESPLAZAMIENTO_DIAS} días), no contra la misma fecha calendario del año "
        f"pasado. Como {DESPLAZAMIENTO_DIAS} días son exactamente 52 semanas, cada día "
        "del período actual cae en el mismo día de la semana que su equivalente en la "
        "comparación — evita distorsionar el análisis comparando, por ejemplo, un lunes "
        "contra un sábado."
    )

    if company not in empresas_disponibles():
        st.info(
            f"Este análisis todavía no está habilitado para {COMPANIES[company][0]}: "
            "primero hay que confirmar la estructura real de su tabla de ventas (no "
            "queremos adivinar nombres de tabla/columna). Pedile al chat de esta empresa "
            "que explore su esquema de ventas y compartilo para habilitarlo acá."
        )
    else:
        hoy = date.today()
        columna_inicio, columna_fin, columna_sucursal = st.columns([1, 1, 1.4])

        with columna_inicio:
            fecha_inicio = st.date_input(
                "Desde",
                value=hoy.replace(day=1),
                max_value=hoy,
                key="lfl_inicio",
            )
        with columna_fin:
            fecha_fin = st.date_input(
                "Hasta",
                value=hoy,
                max_value=hoy,
                key="lfl_fin",
            )
        with columna_sucursal:
            try:
                sucursales = list_branches(database, company)
                # Alcance del usuario: si tiene sucursales asignadas, solo ve
                # esas. Por ahora es un filtro de pantalla; el bloqueo real a
                # nivel de consulta se implementa en la Fase 0.3.
                sucursales = auth.filtrar_sucursales(usuario_sesion, sucursales)
            except Exception as error:
                sucursales = []
                if debug_mode:
                    st.warning(f"No se pudieron cargar las sucursales: {error}")
            sucursal = st.selectbox(
                "Sucursal",
                options=["Todas"] + sucursales,
                key="lfl_sucursal",
            )

        if st.button("Analizar LFL", type="primary", use_container_width=True):
            if fecha_fin < fecha_inicio:
                st.warning("La fecha 'Hasta' no puede ser anterior a 'Desde'.")
            else:
                try:
                    with st.spinner("Calculando comparación LFL..."):
                        resultado = lfl_comparison(
                            database=database,
                            company=company,
                            start=fecha_inicio,
                            end_inclusive=fecha_fin,
                            branch=None if sucursal == "Todas" else sucursal,
                        )
                except Exception as error:
                    st.error("No se pudo calcular el análisis LFL.")
                    if debug_mode:
                        st.exception(error)
                else:
                    st.caption(
                        f"Período actual: {resultado['start'].strftime('%d/%m/%Y')} al "
                        f"{resultado['end'].strftime('%d/%m/%Y')}  ·  "
                        f"Período LFL (52 semanas atrás): "
                        f"{resultado['start_prev'].strftime('%d/%m/%Y')} al "
                        f"{resultado['end_prev'].strftime('%d/%m/%Y')}"
                    )

                    columna_1, columna_2, columna_3 = st.columns(3)
                    columna_1.metric(
                        "Ventas del período actual",
                        f"Gs. {formatear_numero(resultado['venta_actual'])}",
                    )
                    columna_2.metric(
                        "Ventas LFL (52 semanas atrás)",
                        f"Gs. {formatear_numero(resultado['venta_lfl'])}",
                    )
                    variacion = resultado["variacion_pct"]
                    columna_3.metric(
                        "Variación LFL",
                        "Sin comparación" if variacion is None else f"{variacion:+.1f}%",
                    )

                    st.markdown("#### Desglose por día de la semana")
                    tabla = resultado["por_dia_semana"].copy()
                    tabla_mostrar = formatear_dataframe_para_mostrar(
                        tabla.drop(columns=["Variacion_Pct"])
                    )
                    tabla_mostrar["Variación %"] = tabla["Variacion_Pct"].apply(
                        lambda valor: "—" if pd.isna(valor) else f"{valor:+.1f}%".replace(".", ",")
                    )
                    tabla_mostrar = tabla_mostrar.rename(
                        columns={
                            "Venta_Actual": "Venta actual (Gs.)",
                            "Venta_LFL": "Venta LFL (Gs.)",
                            "Facturas_Actual": "Facturas actual",
                            "Facturas_LFL": "Facturas LFL",
                        }
                    )
                    st.dataframe(tabla_mostrar, use_container_width=True, hide_index=True)
                    st.bar_chart(
                        resultado["por_dia_semana"].set_index("Día")[["Venta_Actual", "Venta_LFL"]],
                        use_container_width=True,
                    )


if tab_usuarios is not None:
    with tab_usuarios:
        panel_usuarios.render(usuario_sesion, {
            clave: etiqueta for clave, (etiqueta, _) in TODAS_LAS_COMPANIES.items()
        })

if tab_sincronizacion is not None:
    with tab_sincronizacion:
        if panel_url:
            st.subheader("🔄 Panel de sincronización")
            st.write(f"Abrí el panel completo de {COMPANIES[company][0]} que ya conocés.")
            st.write("Vas a ver la última actualización, el estado de cada vista y los botones Sync, Sincronizar marcadas y Sincronizar todo.")
            st.link_button("Abrir panel completo", panel_url)
            st.caption("También tenés el acceso directo encima de la versión, abajo a la izquierda. Si el acceso venció, recargá Kyber y abrilo de nuevo.")
        else:
            panel_sincronizacion.render(company, COMPANIES[company][0])

if tab_suscripciones is not None:
    with tab_suscripciones:
        panel_suscripciones.render(usuario_sesion)


# ---------------------------------------------------------------------
# EL MODULO DE PRESUPUESTOS VA ULTIMO, A PROPOSITO.
#
# Streamlit ejecuta el script entero en cada recarga, incluido el codigo
# de todas las pestanas, no solo el de la que se esta mirando. El orden
# del codigo NO cambia donde aparece cada pestana en pantalla, pero SI
# cambia que pasa si una frena la ejecucion.
#
# Si este bloque llama a st.stop() -directa o indirectamente- el script
# muere ahi sin dejar error, sin excepcion que atrapar y sin linea en el
# log, y todo lo que venga despues queda dibujado pero vacio. Fue
# exactamente lo que paso con Ejapo: Usuarios y Suscripciones aparecian
# en blanco y no habia rastro de nada en el log.
#
# Estando al final, si vuelve a frenar solo se afecta a si mismo.
# ---------------------------------------------------------------------
if tab_presupuestos is not None:
    with tab_presupuestos:
        # El control por empresa se hace aca y no al armar las pestanas,
        # para que la cantidad de pestanas no cambie al mover el selector
        # de empresa. Ver el comentario en _ve_presupuestos().
        if company not in EMPRESAS_CON_PRESUPUESTOS:
            st.info(
                f"El módulo de presupuestos no aplica a "
                f"{COMPANIES[company][0]}. Cambiá la empresa activa en el "
                "panel de la izquierda."
            )
        else:
            try:
                presupuestos_ui.render_presupuestos()
            except Exception as error:  # noqa: BLE001
                write_log("ERROR", f"[{company}] Fallo el modulo de presupuestos: {error}")
                st.error("No se pudo abrir el módulo de presupuestos.")
                if debug_mode:
                    st.exception(error)
