# -*- coding: utf-8 -*-
"""
Conepasa IA - Cheques diferidos
Capa de calculo: lee el Excel y responde las preguntas.

Idea clave: el MODELO interpreta la pregunta del usuario y elige que funcion
llamar; el CALCULO (conteos y montos) lo hace este codigo. Asi los numeros
siempre son exactos, no "a ojo" del modelo.

Definiciones acordadas:
  - "Entro / se acredito"  -> Estatus ACREDITADO, por FECHA DE PAGO.
  - "Vence / a depositar / pendiente" -> por VTO CHEQUE (vencimiento).
  - Se descartan cheques ANULADOS, LIBRES, sin monto o con fecha invalida.
"""

import os
from pathlib import Path
from datetime import date, timedelta
import pandas as pd

# --- Ubicacion del archivo -------------------------------------------------
# La plataforma corre local en el Zenbook. Recomendado: dejar el Excel en una
# carpeta 'datos' AL LADO de este archivo (dentro de la carpeta del engine).
# Asi la ruta no depende del usuario de Windows ni de como ordene OneDrive.
# Se puede sobreescribir con la variable de entorno CONEPASA_CHEQUES_XLSX.
_AQUI = Path(__file__).resolve().parent
RUTA = os.environ.get(
    "CONEPASA_CHEQUES_XLSX",
    str(_AQUI / "datos" / "CHEQUES DIFERIDOS.xlsx"),
)
# Alternativa, si preferis dejarlo en el Escritorio en vez de junto al engine:
# RUTA = r"C:\Users\eball\OneDrive\Desktop\CLAUDE Informes\CHEQUES DIFERIDOS.xlsx"

HOJA = "CHEQUES DIFERIDOS"   # hoja maestra (una fila por cheque)
FILA_ENCABEZADO = 5          # el encabezado esta en la 6ta fila del Excel (indice 5)
ESTATUS_VALIDOS = {"ACREDITADO", "PENDIENTE"}


def cargar_cheques(ruta: str = None) -> pd.DataFrame:
    """Lee y limpia la hoja maestra. Devuelve un DataFrame listo para consultar."""
    ruta = ruta or RUTA
    try:
        df = pd.read_excel(ruta, sheet_name=HOJA, header=FILA_ENCABEZADO)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"No se encontro el archivo en:\n  {ruta}\n"
            "Verifica la ruta (o la variable CONEPASA_CHEQUES_XLSX) y que "
            "OneDrive lo tenga sincronizado."
        )

    df = df.rename(columns={
        "Cheque Numero": "nro",
        "Emision del CH": "emision",
        "Proveedor": "proveedor",
        "Vto Cheque": "vto",
        "Monto del CH": "monto",
        "Estatus": "estatus",
        "FECHA DE PAGO": "pago",
    })

    for c in ("emision", "vto", "pago"):
        df[c] = pd.to_datetime(df[c], errors="coerce", format="mixed")
        df.loc[df[c].dt.year < 2020, c] = pd.NaT   # descarta fechas basura (ej. 1970)
    df["monto"] = pd.to_numeric(df["monto"], errors="coerce")
    df["proveedor"] = df["proveedor"].astype(str).str.strip()

    df = df[df["estatus"].isin(ESTATUS_VALIDOS)]
    df = df[df["monto"].notna() & (df["monto"] > 0)]
    return df.reset_index(drop=True)


# --- Utilidades ------------------------------------------------------------
def gs(monto) -> str:
    """Formatea un monto en guaranies: 12500000 -> 'Gs. 12.500.000'."""
    return "Gs. " + f"{int(round(monto)):,}".replace(",", ".")


def _detalle(sub: pd.DataFrame) -> dict:
    """Respuesta estandar: cantidad, monto total y detalle por cheque."""
    sub = sub.sort_values("monto", ascending=False)
    return {
        "cantidad": int(len(sub)),
        "monto_total": int(sub["monto"].sum()),
        "monto_total_fmt": gs(sub["monto"].sum()),
        "cheques": [
            {
                "nro": int(r.nro) if pd.notna(r.nro) else None,
                "proveedor": r.proveedor,
                "monto": int(r.monto),
                "monto_fmt": gs(r.monto),
                "vto": r.vto.date().isoformat() if pd.notna(r.vto) else None,
                "pago": r.pago.date().isoformat() if pd.notna(r.pago) else None,
            }
            for r in sub.itertuples()
        ],
    }


# --- Preguntas (cada una es una "herramienta" que puede llamar el modelo) ---
def entraron_ayer(df: pd.DataFrame, hoy: date) -> dict:
    """Cheques que se acreditaron ayer (por fecha de pago)."""
    ayer = pd.Timestamp(hoy - timedelta(days=1))
    sub = df[(df["estatus"] == "ACREDITADO") & (df["pago"] == ayer)]
    return _detalle(sub)


def entran_hoy(df: pd.DataFrame, hoy: date) -> dict:
    """Cheques a depositar hoy: vencimiento hoy y todavia pendientes."""
    h = pd.Timestamp(hoy)
    sub = df[(df["estatus"] == "PENDIENTE") & (df["vto"] == h)]
    return _detalle(sub)


def entraron_en_mes(df: pd.DataFrame, hoy: date) -> dict:
    """Cheques ya acreditados en el mes en curso, hasta la fecha de corte."""
    ini = pd.Timestamp(hoy.replace(day=1))
    h = pd.Timestamp(hoy)
    sub = df[(df["estatus"] == "ACREDITADO") & (df["pago"] >= ini) & (df["pago"] <= h)]
    return _detalle(sub)


def pendientes_del_mes(df: pd.DataFrame, hoy: date) -> dict:
    """Cheques pendientes de deposito con vencimiento en el mes en curso."""
    ini = pd.Timestamp(hoy.replace(day=1))
    fin = ini + pd.offsets.MonthEnd(1)
    sub = df[(df["estatus"] == "PENDIENTE") & (df["vto"] >= ini) & (df["vto"] <= fin)]
    return _detalle(sub)


FUNCIONES = {
    "entraron_ayer": entraron_ayer,
    "entran_hoy": entran_hoy,
    "entraron_en_mes": entraron_en_mes,
    "pendientes_del_mes": pendientes_del_mes,
}

# Esquemas para el modelo (tool use). No requieren parametros: la app le pasa
# la fecha de hoy al ejecutar. El modelo solo elige cual llamar.
HERRAMIENTAS = [
    {"name": "entraron_ayer",
     "description": "Cheques que se acreditaron/cobraron AYER. Devuelve cantidad, monto total y detalle por proveedor.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "entran_hoy",
     "description": "Cheques a depositar HOY (vencen hoy y siguen pendientes). Cantidad, monto y detalle.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "entraron_en_mes",
     "description": "Cheques YA acreditados en lo que va del MES en curso. Cantidad y monto.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "pendientes_del_mes",
     "description": "Cheques PENDIENTES de deposito que vencen dentro del MES en curso. Cantidad y monto.",
     "input_schema": {"type": "object", "properties": {}}},
]


if __name__ == "__main__":
    df = cargar_cheques()
    hoy = date.today()
    print("Cheques operativos:", len(df))
    for nombre, fn in FUNCIONES.items():
        r = fn(df, hoy)
        print(f"{nombre:20s}: {r['cantidad']:3d} cheques  {r['monto_total_fmt']}")
