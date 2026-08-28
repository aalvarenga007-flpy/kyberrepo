# -*- coding: utf-8 -*-
"""
PESTAÑA DE PRESUPUESTOS - Conepasa IA
=====================================

Pantalla para subir la lista que manda el cliente y generar el presupuesto.

Se controla con la variable de entorno MODULO_PRESUPUESTOS:
    MODULO_PRESUPUESTOS=true    -> la pestaña aparece   (Ejapo)
    MODULO_PRESUPUESTOS=false   -> la pestaña no existe (Ekaru)

Para integrarla en la app principal, agregar en app.py:

    from presupuestos_ui import modulo_activo, render_presupuestos

    pestanas = ["Chat", "Informes"]
    if modulo_activo():
        pestanas.append("Presupuestos")
    tabs = st.tabs(pestanas)
    ...
    if modulo_activo():
        with tabs[-1]:
            render_presupuestos()

Tambien se puede correr sola para probar:

    streamlit run presupuestos_ui.py
"""

import os
from datetime import datetime

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

import presupuestador as P

load_dotenv()

EXTENSIONES = ["xlsx", "xls", "xlsm", "csv", "tsv", "pdf", "jpg", "jpeg", "png", "webp"]

# Dos paletas distintas a proposito: la app de Conepasa IA tiene fondo
# oscuro y el Excel fondo blanco. Un pastel claro sobre fondo negro deja el
# texto ilegible, que es exactamente lo que pasaba antes.
COLOR_PANTALLA = {
    "ALTA": "#1B4332",       # verde oscuro
    "MEDIA": "#5C4813",      # ambar oscuro
    "BAJA": "#3A3A3A",       # gris neutro
    "SIN MATCH": "#5C1F1F",  # rojo oscuro
    "OMITIR": "#242424",     # casi el fondo
}


def modulo_activo():
    return os.getenv("MODULO_PRESUPUESTOS", "false").strip().lower() in ("true", "1", "si", "sí")


# ----------------------------------------------------------------- datos

@st.cache_data(ttl=600)
def listar_clientes():
    """Clientes con compras en los ultimos 18 meses, el que mas compro primero."""
    filas = P.consultar(
        "SELECT ruc, razon_social, COUNT(DISTINCT Factura) AS facturas, "
        "       MAX(Fecha_Hora) AS ultima "
        "FROM ventas "
        "WHERE ruc IS NOT NULL AND ruc <> '' AND ruc <> 'X' "
        "  AND Fecha_Hora >= DATE_SUB(CURDATE(), INTERVAL 18 MONTH) "
        "GROUP BY ruc, razon_social "
        "HAVING facturas >= 2 "
        "ORDER BY facturas DESC"
    )
    return pd.DataFrame(filas)


def etiqueta_cliente(fila):
    return f"{fila.razon_social}  ·  RUC {fila.ruc}  ·  {int(fila.facturas)} facturas"


# ----------------------------------------------------------------- pantalla

def render_presupuestos():
    st.subheader("Presupuestos")
    st.caption(
        "Subí la lista que mandó el cliente, en el formato que sea. "
        "El sistema propone los productos y vos revisás antes de emitir."
    )

    # --- 1. archivo
    archivo = st.file_uploader(
        "Lista del cliente",
        type=EXTENSIONES,
        help="Excel, CSV, PDF o foto de WhatsApp. Las fotos se transcriben automáticamente.",
    )

    # --- 2. cliente
    try:
        clientes = listar_clientes()
    except Exception as e:
        st.error(f"No se pudo leer la lista de clientes: {e}")
        return

    col1, col2 = st.columns([3, 2])

    with col1:
        opciones = ["— Cliente nuevo o sin RUC —"] + [
            etiqueta_cliente(f) for f in clientes.itertuples()
        ]
        elegido = st.selectbox(
            "Cliente",
            opciones,
            help="Si el cliente ya compró, el sistema le cotiza lo que suele llevar.",
        )
        ruc = None
        if elegido != opciones[0]:
            ruc = clientes.iloc[opciones.index(elegido) - 1].ruc

    with col2:
        referencia = None
        if ruc is None:
            usar_ref = st.checkbox(
                "Usar otro cliente como referencia",
                help="Para clientes nuevos: tomar como guía lo que compra un cliente parecido.",
            )
            if usar_ref:
                ref_opciones = [f.razon_social for f in clientes.head(60).itertuples()]
                referencia = st.selectbox("Cliente de referencia", ref_opciones)

    if ruc:
        n = len(P.historial_cliente(ruc))
        if n:
            st.success(f"Cliente con historial: {n} productos comprados en el último año.")
        else:
            st.info("Este cliente no tiene compras en el último año. Se usa el ranking general.")

    # --- 3. generar
    if not archivo:
        st.stop()

    if not st.button("Generar presupuesto", type="primary"):
        st.stop()

    with st.spinner("Leyendo el archivo y buscando los productos…"):
        try:
            ruta = P.guardar_entrada(archivo.name, archivo.getbuffer())
            salida = P.generar(ruta, ruc=ruc, referencia=referencia)
        except Exception as e:
            st.error(f"No se pudo procesar el archivo: {e}")
            st.caption(
                "Si es un PDF escaneado o una foto poco legible, probá con una imagen más nítida."
            )
            st.stop()

    # Que se vea que se leyo: si una pestaña falla, el usuario tiene que
    # enterarse aca y no descubrirlo cuando el cliente reclame lo que falta.
    diag = getattr(P, "ULTIMA_LECTURA", {})
    hojas, fallas = diag.get("hojas") or [], diag.get("errores") or []
    if hojas:
        st.caption("Pestañas leídas:  " +
                   "   ·   ".join(f"**{n}**: {c} líneas" for n, c in hojas))
    if fallas:
        st.error("No se pudieron leer estas partes del archivo: "
                 + " ; ".join(fallas)
                 + ".  Revisá que no falten productos.")

    # Un solo Excel con una pestaña por destino. En pantalla se muestran como
    # solapas, y el boton de descarga baja el archivo entero.
    hojas_salida = _hojas_del_libro(salida)
    if len(hojas_salida) > 1:
        st.info(
            f"El pedido venía separado en {len(hojas_salida)} destinos. "
            "Cada uno es una pestaña del mismo Excel."
        )
        _resumen_destinos(hojas_salida, salida)
        for solapa, nombre in zip(st.tabs(hojas_salida), hojas_salida):
            with solapa:
                _mostrar_resultado(_leer_salida(salida, nombre), salida, nombre)
        _boton_descarga(salida)
        st.stop()

    df = _leer_salida(salida, hojas_salida[0] if hojas_salida else 0)
    st.session_state["presu_df"] = df
    st.session_state["presu_salida"] = salida

    _mostrar_resultado(df, salida)
    _boton_descarga(salida)


def _hojas_del_libro(ruta):
    """Nombres de las pestañas del Excel, sin la de RESUMEN."""
    try:
        nombres = pd.ExcelFile(ruta).sheet_names
    except Exception:  # noqa: BLE001
        return []
    return [n for n in nombres if n.upper() != "RESUMEN"]


def _resumen_destinos(nombres, ruta):
    """Tabla con el total de cada destino, para comparar de un vistazo."""
    filas = []
    for n in nombres:
        d = _leer_salida(ruta, n)
        filas.append({
            "Destino": n,
            "Líneas": len(d),
            "Alta": int((d.confianza == "ALTA").sum()),
            "Media": int((d.confianza == "MEDIA").sum()),
            "Sin match": int((d.confianza == "SIN MATCH").sum()),
            "Total Gs.": f"{d['subtotal'].fillna(0).sum():,.0f}".replace(",", "."),
        })
    total = sum(_leer_salida(ruta, n)["subtotal"].fillna(0).sum() for n in nombres)
    st.dataframe(pd.DataFrame(filas), width="stretch", hide_index=True)
    st.metric("Total general", f"Gs. {total:,.0f}".replace(",", "."))


def _boton_descarga(salida):
    with open(salida, "rb") as f:
        st.download_button(
            "Descargar el presupuesto en Excel",
            f.read(),
            file_name=os.path.basename(salida),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            key=f"bajar_{os.path.basename(salida)}",
        )
    st.caption(
        f"El archivo también queda guardado en {P.DIR_SALIDA}. "
        "Las alternativas de cada línea están en la última columna del Excel."
    )


def _leer_salida(ruta, hoja=0):
    """Lee una pestaña del Excel generado y deja las columnas listas."""
    df = pd.read_excel(ruta, sheet_name=hoja, skiprows=2)
    # El Excel termina con una fila de TOTAL. Si no se descarta, la pantalla la
    # cuenta como un producto mas y el total sale al doble.
    if "pide" in df.columns:
        df = df[df["pide"].notna() & (df["pide"].astype(str).str.strip() != "")]
    df = df.reset_index(drop=True)
    for col in ("aviso", "origen", "alternativas", "producto"):
        if col in df.columns:
            df[col] = df[col].fillna("")
    for col in ("cantidad", "precio", "subtotal", "costo", "stock", "codprod"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _pintar_fila(fila):
    """Colorea la fila entera segun la confianza, con texto siempre legible."""
    fondo = COLOR_PANTALLA.get(fila.get("confianza"), "")
    if not fondo:
        return [""] * len(fila)
    return [f"background-color: {fondo}; color: #FFFFFF"] * len(fila)


def _mostrar_resultado(df, salida, hoja=""):
    conteo = df["confianza"].value_counts()

    c = st.columns(5)
    for col, nivel, ayuda in zip(
        c,
        ["ALTA", "MEDIA", "BAJA", "SIN MATCH", "OMITIR"],
        [
            "El cliente siempre compra este producto",
            "El cliente alterna marcas: elegí vos",
            "Cliente nuevo: sugerencia del ranking general",
            "No está en el diccionario: cargar a mano",
            "El cliente no puso cantidad",
        ],
    ):
        col.metric(nivel.title(), int(conteo.get(nivel, 0)), help=ayuda)

    st.caption(
        "🟢 Alta: el cliente siempre lo compra  ·  "
        "🟡 Media: alterna marcas, elegí vos  ·  "
        "⬜ Baja: sugerencia general  ·  "
        "🔴 Sin match: cargar a mano"
    )

    avisos = df["aviso"].fillna("").ne("").sum()
    if avisos:
        st.warning(f"{avisos} líneas tienen un aviso de precio o de empaque. Revisalas.")

    perdidas = df["aviso"].fillna("").str.contains("PERDIDA").sum()
    if perdidas:
        st.error(
            f"{perdidas} productos están por debajo del costo. "
            "No los cotices sin corregir el precio."
        )

    total = float(df["subtotal"].fillna(0).sum())
    st.metric("Total estimado", f"Gs. {total:,.0f}".replace(",", "."))
    st.caption("Estimado: no incluye las líneas sin resolver.")

    # Streamlit no permite elegir el separador de miles, y con format="%d"
    # los numeros salen pegados. Se arman como texto con punto, que es la
    # convencion en Paraguay.
    vista = df.copy()
    for col in ("precio", "subtotal"):
        if col in vista.columns:
            vista[col] = vista[col].map(
                lambda v: "" if pd.isna(v) else f"{v:,.0f}".replace(",", "."))
    if "cantidad" in vista.columns:
        vista["cantidad"] = vista["cantidad"].map(
            lambda v: "" if pd.isna(v) else (f"{v:,.0f}".replace(",", ".")
                                             if float(v).is_integer()
                                             else f"{v:,.2f}".replace(",", "@")
                                                             .replace(".", ",")
                                                             .replace("@", ".")))

    columnas = [c for c in
                ["pide", "cantidad_pedida", "confianza", "producto", "cantidad",
                 "precio", "subtotal", "aviso"] if c in df.columns]

    st.dataframe(
        vista[columnas].style.apply(_pintar_fila, axis=1),
        width="stretch",
        height=520,
        key=f"tabla_{os.path.basename(salida)}_{hoja}",
        column_config={
            "pide": "Pidió",
            "cantidad_pedida": "Cantidad pedida",
            "confianza": "Confianza",
            "producto": "Producto sugerido",
            "cantidad": st.column_config.TextColumn("Cant.", width="small"),
            "precio": st.column_config.TextColumn("Precio", width="small"),
            "subtotal": st.column_config.TextColumn("Subtotal", width="small"),
            "aviso": "Aviso",
        },
    )



# ----------------------------------------------------------------- solo

if __name__ == "__main__":
    st.set_page_config(page_title="Presupuestos · Conepasa IA", layout="wide")
    if not modulo_activo():
        st.warning(
            "El módulo de presupuestos está desactivado. "
            "Para activarlo, poné MODULO_PRESUPUESTOS=true en el archivo .env"
        )
    else:
        render_presupuestos()
