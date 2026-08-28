"""Formato numérico estándar de la plataforma: punto como separador de
miles y coma como separador decimal (convención paraguaya/es-LA), en
cualquier tabla que se muestre en pantalla.

Regla del usuario: todos los informes numéricos que se muestren en la
plataforma deben usar separador de miles — sin excepción.

Importante: este formato es solo para lo que se VE en pantalla. Los
archivos descargados (Excel/CSV) siguen llevando los números en formato
numérico real, sin convertir a texto, para que se puedan sumar/ordenar/
graficar en Excel sin tener que reformatearlos primero.
"""

from __future__ import annotations

import pandas as pd


def formatear_numero(valor) -> str:
    if valor is None:
        return "—"
    if isinstance(valor, float) and pd.isna(valor):
        return "—"
    if isinstance(valor, bool):
        return str(valor)
    if isinstance(valor, (int, float)):
        numero = float(valor)
        if numero.is_integer():
            return f"{numero:,.0f}".replace(",", ".")
        return f"{numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return str(valor)


def formatear_dataframe_para_mostrar(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Devuelve una COPIA del dataframe con las columnas numéricas
    formateadas con separador de miles, lista para st.dataframe(). No toca
    el dataframe original (el que se usa para exportar a Excel/CSV)."""
    if dataframe is None or dataframe.empty:
        return dataframe

    formateado = dataframe.copy()
    for columna in formateado.columns:
        numerico = pd.to_numeric(formateado[columna], errors="coerce")
        columna_es_numerica = (
            numerico.notna().sum() == formateado[columna].notna().sum()
            and numerico.notna().any()
        )
        if columna_es_numerica:
            formateado[columna] = numerico.apply(formatear_numero)
    return formateado
