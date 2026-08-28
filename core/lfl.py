"""Análisis LFL (Like-for-Like) por día de la semana / "calendario homologado
retail": compara un período contra el mismo período 52 semanas atrás (364
días), en lugar de contra la misma fecha calendario un año atrás.

Por qué 364 días y no 365: 364 = 52 × 7. Al desplazar el período exactamente
52 semanas hacia atrás, cada fecha del período actual cae en el mismo día de
la semana que su equivalente en el período de comparación. Comparar contra
un año calendario exacto (365 o 366 días) desalinea el día de la semana y
distorsiona el análisis en cualquier negocio con estacionalidad semanal
marcada (un lunes no vende como un sábado).

JORNADA OPERATIVA (no fecha de calendario)
------------------------------------------
El LFL se calcula sobre la JORNADA DE TRABAJO, no sobre la fecha impresa en
la factura. Las ventas del viernes son todas las que arrancaron ese día,
aunque la caja se cierre a las 02:00 del sábado.

Esto se resuelve en la vista `v_ventas_jornada_lineas` (ver el archivo
vistas_jornada_ekaru.sql), que agrega una columna `jornada` calculada como
DATE(Fecha_Hora - INTERVAL 3 HOUR). El corte a las 03:00 se eligió con
datos reales: los locales nocturnos nunca facturan después de las 02:59 y
el Comedor nunca antes de las 04:00, así que la hora 3 está vacía y es el
punto de corte natural.

Sin esto, una noche de viernes que cierra a las 02:07 quedaba partida al
medio: el informe mostraba Gs. 9.322.150 cuando la sucursal había vendido
Gs. 20.162.800 en el turno.

Estas consultas están escritas y probadas a mano — a diferencia del chat,
que genera SQL dinámicamente — porque es un reporte de uso recurrente: acá
conviene que el cálculo sea fijo, rápido y 100% reproducible, no generado de
nuevo en cada consulta.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from sqlalchemy import text

from core.db import get_engine

DIAS_NOMBRE = {
    1: "Domingo", 2: "Lunes", 3: "Martes", 4: "Miércoles",
    5: "Jueves", 6: "Viernes", 7: "Sábado",
}
ORDEN_SEMANA = [2, 3, 4, 5, 6, 7, 1]  # Lunes -> Domingo, para mostrar en ese orden

DESPLAZAMIENTO_DIAS = 364  # 52 semanas exactas

# Configuración por empresa: nombre de la tabla de ventas y de sus columnas
# clave, validadas contra el sistema de reportes existente (ver
# BUSINESS_NOTES en ai/agent.py para el detalle de cómo se confirmó cada
# una). Si una empresa no está acá, el análisis LFL todavía no está
# disponible para ella — hay que confirmar su esquema real antes de
# agregarla, no adivinarlo.
#
# Ekarú apunta a la VISTA v_ventas_jornada_lineas, no a la tabla `ventas`:
#   - `jornada`  -> fecha de la jornada operativa (corte 03:00), no la fecha
#                   de calendario. Es lo que hace que el viernes incluya las
#                   mesas cerradas a las 02:00 del sábado.
#   - `idventa`  -> identificador de comprobante. Se usa en lugar de
#                   `Factura` porque las REMISIONES (vales de premiación,
#                   comedor de funcionarios) no tienen número de factura y
#                   quedaban contadas como cero.
#   - la vista ya excluye QUINCHO, CANTINA y ADMINISTRACION, y ya renombra
#     CASA MATRIZ como SPORTBAR.
#
# Ejapo (Comercial San José) usa EL MISMO ERP que Ekarú y su tabla `ventas`
# tiene exactamente las mismas columnas clave (Fecha_Hora, Factura,
# Condicion_Venta, subtotal, idventa, Cajero, Sucursal). Confirmado contra
# information_schema, no adivinado. Por eso apunta a la misma vista, creada
# en su base con vistas_jornada_ejapo.sql.
CONFIG_POR_EMPRESA = {
    "ekaru": {
        "tabla": "v_ventas_jornada_lineas",
        "fecha": "jornada",
        "monto": "subtotal",
        "factura": "idventa",
        "sucursal": "sucursal",
    },
    "ejapo": {
        "tabla": "v_ventas_jornada_lineas",
        "fecha": "jornada",
        "monto": "subtotal",
        "factura": "idventa",
        "sucursal": "sucursal",
    },
}


def empresas_disponibles() -> list[str]:
    return list(CONFIG_POR_EMPRESA)


def _config(company: str) -> dict:
    config = CONFIG_POR_EMPRESA.get(company)
    if not config:
        raise ValueError(
            f"El análisis LFL todavía no está validado para la empresa '{company}'."
        )
    return config


def list_branches(database: str, company: str) -> list[str]:
    config = _config(company)
    sql = f"""
SELECT DISTINCT TRIM({config['sucursal']}) AS Sucursal
FROM {config['tabla']}
WHERE {config['sucursal']} IS NOT NULL AND TRIM({config['sucursal']}) <> ''
ORDER BY Sucursal
"""
    engine = get_engine(database)
    with engine.connect() as connection:
        resultado = pd.read_sql(text(sql), connection)
    return resultado["Sucursal"].tolist()


def _rango_sql(start: date, end_inclusive: date) -> tuple[str, str]:
    return start.isoformat(), (end_inclusive + timedelta(days=1)).isoformat()


def _fechas_por_dia_semana(start: date, end_inclusive: date) -> dict[int, list[date]]:
    """Para cada día de la semana (convención MySQL: 1=domingo..7=sábado),
    lista las fechas concretas de [start, end_inclusive] que caen en él."""
    fechas: dict[int, list[date]] = {}
    actual = start
    while actual <= end_inclusive:
        # date.isoweekday(): 1=lunes..7=domingo -> convención MySQL DAYOFWEEK: 1=domingo..7=sábado
        dia_num = actual.isoweekday() % 7 + 1
        fechas.setdefault(dia_num, []).append(actual)
        actual += timedelta(days=1)
    return fechas


def _etiqueta_dia(nombre: str, fechas: list[date]) -> str:
    if not fechas:
        return nombre
    if len(fechas) == 1:
        return f"{nombre} {fechas[0].day}"
    if fechas[0].month == fechas[-1].month:
        return f"{nombre} {fechas[0].day}-{fechas[-1].day}"
    return f"{nombre} {fechas[0].strftime('%d/%m')}–{fechas[-1].strftime('%d/%m')}"


def _filtro_sucursal(config: dict, branch: str | None) -> str:
    if not branch:
        return ""
    seguro = branch.replace("\\", "\\\\").replace("'", "''")
    return f" AND UPPER(TRIM({config['sucursal']})) = UPPER('{seguro}')"


def _totales(database: str, config: dict, start: date, end_inclusive: date, branch: str | None) -> dict:
    start_str, end_next_str = _rango_sql(start, end_inclusive)
    sql = f"""
SELECT
    ROUND(COALESCE(SUM({config['monto']}), 0), 0) AS Venta,
    COUNT(DISTINCT {config['factura']}) AS Facturas
FROM {config['tabla']}
WHERE {config['fecha']} >= '{start_str}' AND {config['fecha']} < '{end_next_str}'\
{_filtro_sucursal(config, branch)}
"""
    engine = get_engine(database)
    with engine.connect() as connection:
        fila = pd.read_sql(text(sql), connection).iloc[0]
    return {"Venta": float(fila["Venta"] or 0), "Facturas": int(fila["Facturas"] or 0)}


def _por_dia_semana(
    database: str, config: dict, start: date, end_inclusive: date, branch: str | None
) -> pd.DataFrame:
    start_str, end_next_str = _rango_sql(start, end_inclusive)
    sql = f"""
SELECT
    DAYOFWEEK({config['fecha']}) AS Dia_Num,
    ROUND(COALESCE(SUM({config['monto']}), 0), 0) AS Venta,
    COUNT(DISTINCT {config['factura']}) AS Facturas
FROM {config['tabla']}
WHERE {config['fecha']} >= '{start_str}' AND {config['fecha']} < '{end_next_str}'\
{_filtro_sucursal(config, branch)}
GROUP BY DAYOFWEEK({config['fecha']})
"""
    engine = get_engine(database)
    with engine.connect() as connection:
        return pd.read_sql(text(sql), connection)


def lfl_comparison(
    database: str,
    company: str,
    start: date,
    end_inclusive: date,
    branch: str | None = None,
) -> dict:
    """Compara [start, end_inclusive] contra el mismo rango 52 semanas atrás,
    en total y desglosado por día de la semana.

    Las fechas se interpretan como JORNADAS OPERATIVAS: el viernes 7 incluye
    todo lo vendido desde su apertura hasta el cierre de caja de la
    madrugada del sábado."""
    if end_inclusive < start:
        raise ValueError("La fecha final no puede ser anterior a la fecha inicial.")

    config = _config(company)

    start_prev = start - timedelta(days=DESPLAZAMIENTO_DIAS)
    end_prev = end_inclusive - timedelta(days=DESPLAZAMIENTO_DIAS)

    actual = _totales(database, config, start, end_inclusive, branch)
    lfl = _totales(database, config, start_prev, end_prev, branch)

    variacion_pct = (
        round((actual["Venta"] - lfl["Venta"]) / lfl["Venta"] * 100, 1)
        if lfl["Venta"]
        else None
    )

    actual_dia = _por_dia_semana(database, config, start, end_inclusive, branch)
    lfl_dia = _por_dia_semana(database, config, start_prev, end_prev, branch)

    fechas_actuales = _fechas_por_dia_semana(start, end_inclusive)

    tabla = pd.DataFrame({"Dia_Num": ORDEN_SEMANA})
    tabla["Día"] = tabla["Dia_Num"].apply(
        lambda dia_num: _etiqueta_dia(DIAS_NOMBRE[dia_num], fechas_actuales.get(dia_num, []))
    )
    tabla = tabla.merge(
        actual_dia.rename(columns={"Venta": "Venta_Actual", "Facturas": "Facturas_Actual"}),
        on="Dia_Num", how="left",
    ).merge(
        lfl_dia.rename(columns={"Venta": "Venta_LFL", "Facturas": "Facturas_LFL"}),
        on="Dia_Num", how="left",
    )
    for columna in ("Venta_Actual", "Facturas_Actual", "Venta_LFL", "Facturas_LFL"):
        tabla[columna] = tabla[columna].fillna(0)

    tabla["Variacion_Pct"] = tabla.apply(
        lambda fila: (
            round((fila["Venta_Actual"] - fila["Venta_LFL"]) / fila["Venta_LFL"] * 100, 1)
            if fila["Venta_LFL"]
            else None
        ),
        axis=1,
    )

    return {
        "start": start,
        "end": end_inclusive,
        "start_prev": start_prev,
        "end_prev": end_prev,
        "venta_actual": actual["Venta"],
        "venta_lfl": lfl["Venta"],
        "facturas_actual": actual["Facturas"],
        "facturas_lfl": lfl["Facturas"],
        "variacion_pct": variacion_pct,
        "por_dia_semana": tabla.drop(columns=["Dia_Num"]),
    }
