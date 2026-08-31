"""
core/estilos.py
================
Ajustes visuales para que la app se use bien desde el celular.

Es CSS puro inyectado al inicio del script. No cambia ningun
comportamiento ni ninguna consulta: si manana se quita este archivo, la
app sigue funcionando exactamente igual, solo que peor en pantalla
chica.

DE DONDE SALEN LOS DOS CORTES (medidos, no estimados)
-----------------------------------------------------
    Samsung Z Fold 7 cerrado ....  480 px
    Samsung Z Fold 7 abierto ....  874 px
    iPhone 14 Pro ...............  393 px
    Pixel 7 .....................  412 px
    Notebook ....................  1200 px o mas

De ahi los dos tramos:

  hasta 900 px  ->  Fold abierto y tablets. Ajuste moderado.
  hasta 520 px  ->  Celulares comunes y Fold cerrado. Ajuste fuerte.

Con un celular comun se aplican LOS DOS, que es lo buscado: cuanto mas
angosta la pantalla, mas agresivo el ajuste. Una notebook no entra en
ninguno y se ve exactamente igual que siempre.

Ojo: si el navegador del celular tiene activado "Sitio para
computadoras", reporta mas de 1200 px y estas reglas NO se aplican. No
es un error del codigo; es el navegador mintiendo sobre su tamano.

LA DECISION DE FONDO EN LAS TABLAS
----------------------------------
Una tabla de ventas por sucursal tiene 7 columnas. En 400 px no entran,
y hay dos caminos:

  a) Dejar que cada celda parta en varias lineas. Es lo que pasa hoy:
     "Romana Pizza Birra - Sajonia" ocupa cuatro renglones y la fila se
     vuelve ilegible.

  b) Forzar una linea por celda y que la tabla se deslice de costado.

Se elige (b). Es como muestran las tablas anchas todas las apps del
banco: se lee la primera columna fija en la cabeza del usuario y se
arrastra para ver el resto.
"""

from __future__ import annotations

import streamlit as st


_CSS = """
/* ==================================================================
   TODAS LAS PANTALLAS
   ================================================================== */

/* El boton "Desplegar" (Deploy) es de Streamlit Cloud y no sirve para
   nada en una instalacion propia. Delante de un cliente solo genera la
   pregunta "que es eso?". Se oculta siempre, tambien en escritorio. */
[data-testid="stAppDeployButton"],
[data-testid="stToolbarActions"] {
    display: none !important;
}

/* Subtitulo de presentacion. En escritorio se ve como una nota gris
   discreta; en celular se oculta (ver mas abajo). */
.cnp-subtitulo {
    font-size: 0.85rem;
    line-height: 1.4;
    color: rgba(140, 150, 165, 0.95);
    margin-top: -0.4rem;
    margin-bottom: 0.8rem;
}

/* Version funcional siempre visible en la esquina marcada del sidebar. */
.kyber-sidebar-footer {
    position: fixed;
    left: 1.75rem;
    bottom: 0.75rem;
    z-index: 1000;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    max-width: calc(100vw - 3.5rem);
}
.kyber-panel-link {
    padding: 0.65rem 0.75rem;
    background: #526fe0;
    color: white !important;
    border-radius: 0.5rem;
    text-decoration: none !important;
    font-size: 0.78rem;
    box-shadow: 0 1px 6px #0002;
}
.kyber-panel-link:focus-visible { outline: 3px solid #9dafff; }
[data-testid="stSidebarUserContent"] { padding-bottom: 7rem; }
.kyber-sidebar-version {
    padding: 0.2rem 0.45rem;
    border-radius: 0.45rem;
    background: var(--secondary-background-color);
    color: var(--text-color);
    opacity: 0.68;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.02em;
}


/* ==================================================================
   TRAMO 1 - FOLD ABIERTO Y TABLETS (hasta 900 px)
   ================================================================== */
@media (max-width: 900px) {

    /* --- Espacio util -------------------------------------------- */
    /* Streamlit deja mucho aire arriba y a los costados, pensado para
       monitores. En un celular ese aire es pantalla desperdiciada. */
    .block-container,
    [data-testid="stAppViewContainer"] .block-container,
    [data-testid="stMainBlockContainer"] {
        padding-top: 1.1rem !important;
        padding-left: 0.85rem !important;
        padding-right: 0.85rem !important;
        padding-bottom: 5rem !important;
    }

    /* --- Titulos -------------------------------------------------- */
    /* "Conepasa AI - Motor Claude" ocupaba dos lineas completas. */
    h1, [data-testid="stHeading"] h1 {
        font-size: 1.45rem !important;
        line-height: 1.25 !important;
        margin-bottom: 0.3rem !important;
    }
    h2 { font-size: 1.18rem !important; line-height: 1.3 !important; }
    h3 { font-size: 1.02rem !important; line-height: 1.3 !important; }

    /* El parrafo de presentacion desaparece: son tres renglones que un
       gerente no necesita leer cada vez que abre la app en el celular. */
    .cnp-subtitulo {
        display: none !important;
    }

    /* El resto de los textos chicos (leyendas del LFL, pie del login)
       si se mantienen, solo un poco mas compactos. */
    [data-testid="stCaptionContainer"],
    [data-testid="stCaptionContainer"] p {
        font-size: 0.76rem !important;
        line-height: 1.35 !important;
    }

    /* --- Cabecera de Streamlit ------------------------------------ */
    /* La franja de arriba mide casi 60 px y solo contiene el boton de
       la barra lateral. Se achica a la mitad y el contenido sube. */
    [data-testid="stHeader"] {
        height: 2.6rem !important;
        min-height: 2.6rem !important;
        background: transparent !important;
    }
    [data-testid="stAppViewContainer"] > .main,
    [data-testid="stMain"] {
        padding-top: 0 !important;
    }

    /* --- Barra lateral -------------------------------------------- */
    [data-testid="stSidebar"] {
        min-width: 255px !important;
        max-width: 78vw !important;
    }
    [data-testid="stSidebar"] .block-container,
    [data-testid="stSidebarUserContent"] {
        padding-top: 1.2rem !important;
        padding-left: 0.9rem !important;
        padding-right: 0.9rem !important;
    }

    /* --- TABLAS: una linea por celda, deslizables de costado ------ */
    [data-testid="stMarkdownContainer"] table,
    .stMarkdown table,
    div[data-testid="stMarkdown"] table {
        display: block !important;
        width: 100% !important;
        overflow-x: auto !important;
        white-space: nowrap !important;
        font-size: 0.78rem !important;
        -webkit-overflow-scrolling: touch;
    }
    [data-testid="stMarkdownContainer"] table th,
    [data-testid="stMarkdownContainer"] table td,
    .stMarkdown table th,
    .stMarkdown table td {
        white-space: nowrap !important;
        padding: 0.35rem 0.6rem !important;
    }

    /* Las tablas interactivas (descargas, LFL) tambien achican letra */
    [data-testid="stDataFrame"],
    [data-testid="stDataFrameResizable"] {
        font-size: 0.78rem !important;
    }

    /* --- Metricas del analisis LFL -------------------------------- */
    /* "Gs. 481.036.701" no entra en el tamano por defecto y se corta */
    [data-testid="stMetricValue"] {
        font-size: 1.05rem !important;
        line-height: 1.25 !important;
    }
    [data-testid="stMetricLabel"] p {
        font-size: 0.74rem !important;
        line-height: 1.2 !important;
    }

    /* --- Botones y campos ----------------------------------------- */
    /* 44 px es el minimo para que un dedo acierte comodo. */
    .stButton button,
    .stDownloadButton button,
    [data-testid="stFormSubmitButton"] button {
        min-height: 44px !important;
        font-size: 0.92rem !important;
    }

    /* iOS hace zoom automatico si un campo tiene letra menor a 16 px.
       Se fija en 16 para que no salte la pantalla al tocar el campo. */
    .stTextInput input,
    .stDateInput input,
    [data-testid="stChatInputTextArea"] {
        font-size: 16px !important;
    }

    /* --- Mensajes del chat ---------------------------------------- */
    [data-testid="stChatMessage"] {
        padding: 0.65rem 0.7rem !important;
    }
    [data-testid="stChatMessageContent"] p,
    [data-testid="stChatMessage"] p,
    [data-testid="stChatMessage"] li {
        font-size: 0.92rem !important;
        line-height: 1.45 !important;
    }

    /* --- Pestanas -------------------------------------------------- */
    [data-testid="stTabs"] button p {
        font-size: 0.88rem !important;
    }
}

/* ==================================================================
   TRAMO 2 - CELULARES (hasta 520 px)
   Incluye el Fold cerrado (480), iPhone (393) y Pixel (412).
   Se suma a lo del tramo 1, no lo reemplaza.
   ================================================================== */
@media (max-width: 520px) {
    h1, [data-testid="stHeading"] h1 {
        font-size: 1.18rem !important;
        margin-bottom: 0.15rem !important;
    }

    /* Cabecera todavia mas compacta: en 480 px cada pixel de alto
       cuenta, porque lo que se gana arriba es una fila mas de tabla. */
    [data-testid="stHeader"] {
        height: 2.3rem !important;
        min-height: 2.3rem !important;
    }
    .block-container,
    [data-testid="stAppViewContainer"] .block-container,
    [data-testid="stMainBlockContainer"] {
        padding-top: 0.6rem !important;
    }

    /* En 480 px, un sidebar de 255 se come mas de la mitad. */
    [data-testid="stSidebar"] {
        min-width: 230px !important;
        max-width: 84vw !important;
    }

    .block-container,
    [data-testid="stAppViewContainer"] .block-container,
    [data-testid="stMainBlockContainer"] {
        padding-left: 0.65rem !important;
        padding-right: 0.65rem !important;
    }
    [data-testid="stMarkdownContainer"] table,
    .stMarkdown table {
        font-size: 0.72rem !important;
    }
    [data-testid="stMetricValue"] {
        font-size: 0.95rem !important;
    }
}
"""


def aplicar_estilos() -> None:
    """
    Inyecta el CSS. Se llama una vez, apenas arranca el script, antes
    de dibujar cualquier cosa (incluida la pantalla de login).
    """
    st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)
