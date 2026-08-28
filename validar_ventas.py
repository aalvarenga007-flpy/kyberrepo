"""Validación de los datos de ventas de una empresa de Conepasa AI.

Sirve para AMBAS empresas (Ekarú y Ejapo). No define tabla ni columnas por su
cuenta: las lee de CONFIG_POR_EMPRESA en core/lfl.py, la MISMA fuente de verdad
que usa el análisis LFL y (vía BUSINESS_NOTES) el chat y el resumen diario. Así,
si una columna se corrige en un solo lugar, este validador queda alineado solo.

Qué informa, para la fecha elegida:
  - total de ventas del día  = SUM(monto)          (monto = columna validada por empresa)
  - cantidad de comprobantes = COUNT(DISTINCT factura)
  - ticket promedio, rango mín/máx por comprobante
  - top 10 comprobantes más altos
  - desglose por sucursal
  - comparación con el mismo día de la semana anterior (fecha - 7 días)
Y una sección de "salud de la tabla" (sobre todo el histórico) que detecta las
trampas que ya nos mordieron: comprobantes con cantidad de líneas anómala
(síntoma de duplicación de filas) y montos negativos/cero.

Corre en SOLO LECTURA (usuario conepasa_readonly). No modifica nada.

Uso:
    python validar_ventas.py --empresa ejapo
    python validar_ventas.py --empresa ekaru --fecha 2026-08-03
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta

# La consola de Windows suele venir en codepage 1252 y rompe los acentos.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import text

from core.config import settings
from core.db import get_engine
from core.lfl import CONFIG_POR_EMPRESA


def _gs(valor) -> str:
    """1403940380 -> 'Gs. 1.403.940.380'."""
    try:
        return "Gs. " + f"{float(valor):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return str(valor)


def _database_de(empresa: str) -> str | None:
    """Mapea la empresa a su base según core/config.py (ekaru_database / ejapo_database)."""
    return getattr(settings, f"{empresa}_database", None)


def _titulo(texto: str) -> None:
    print("\n" + "=" * 70)
    print(texto)
    print("=" * 70)


def _totales_dia(conn, cfg: dict, dia: date) -> dict:
    """SUM(monto) y COUNT(DISTINCT factura) del día, usando SOLO columnas de config."""
    ini, fin = dia.isoformat(), (dia + timedelta(days=1)).isoformat()
    rango = f"{cfg['fecha']} >= :ini AND {cfg['fecha']} < :fin"
    fila = conn.execute(text(f"""
        SELECT
            COUNT(*)                              AS lineas,
            COUNT(DISTINCT {cfg['factura']})      AS comprobantes,
            ROUND(COALESCE(SUM({cfg['monto']}), 0)) AS total
        FROM {cfg['tabla']} WHERE {rango}
    """), {"ini": ini, "fin": fin}).mappings().first()
    return dict(fila)


def validar_dia(conn, cfg: dict, dia: date) -> None:
    ini, fin = dia.isoformat(), (dia + timedelta(days=1)).isoformat()
    params = {"ini": ini, "fin": fin}
    rango = f"{cfg['fecha']} >= :ini AND {cfg['fecha']} < :fin"

    _titulo(f"VENTAS DEL DÍA · {dia.strftime('%A %d/%m/%Y')}")
    tot = _totales_dia(conn, cfg, dia)
    lineas, comprobantes, total = tot["lineas"], tot["comprobantes"], float(tot["total"])
    ticket = total / comprobantes if comprobantes else 0
    print(f"Líneas de detalle          : {lineas}")
    print(f"Comprobantes (DISTINCT {cfg['factura']}) : {comprobantes}")
    print(f"Total de ventas SUM({cfg['monto']})       : {_gs(total)}")
    print(f"Ticket promedio            : {_gs(ticket)}")

    # rango de montos por comprobante
    rng = conn.execute(text(f"""
        SELECT MIN(t) mn, MAX(t) mx FROM (
            SELECT {cfg['factura']} AS c, SUM({cfg['monto']}) AS t
            FROM {cfg['tabla']} WHERE {rango} GROUP BY {cfg['factura']}
        ) x
    """), params).mappings().first()
    print(f"Comprobante más chico      : {_gs(rng['mn'])}")
    print(f"Comprobante más grande     : {_gs(rng['mx'])}")

    # por sucursal
    _titulo("POR SUCURSAL")
    for r in conn.execute(text(f"""
        SELECT {cfg['sucursal']} AS suc, COUNT(DISTINCT {cfg['factura']}) comp,
               ROUND(SUM({cfg['monto']})) total
        FROM {cfg['tabla']} WHERE {rango}
        GROUP BY {cfg['sucursal']} ORDER BY total DESC
    """), params).mappings():
        print(f"  {str(r['suc'])[:32]:<34} comprobantes={r['comp']:<5} {_gs(r['total'])}")

    # top 10 comprobantes
    _titulo("TOP 10 COMPROBANTES MÁS ALTOS DEL DÍA")
    for r in conn.execute(text(f"""
        SELECT {cfg['factura']} AS factura, COUNT(*) lineas,
               ROUND(SUM({cfg['monto']})) total
        FROM {cfg['tabla']} WHERE {rango}
        GROUP BY {cfg['factura']} ORDER BY total DESC LIMIT 10
    """), params).mappings():
        print(f"  {str(r['factura']):<20} líneas={r['lineas']:<4} {_gs(r['total'])}")

    # comparación con el mismo día de la semana anterior (lo que hace el resumen diario)
    previo = dia - timedelta(days=7)
    tot_prev = _totales_dia(conn, cfg, previo)
    _titulo(f"COMPARACIÓN SEMANAL · {dia.strftime('%d/%m')} vs {previo.strftime('%d/%m')} (7 días antes)")
    tp = float(tot_prev["total"])
    print(f"{dia.isoformat()} : {_gs(total)} ({comprobantes} comprobantes)")
    print(f"{previo.isoformat()} : {_gs(tp)} ({tot_prev['comprobantes']} comprobantes)")
    if tp:
        print(f"Variación            : {(total - tp) / tp * 100:+.1f}%")
    else:
        print("Variación            : sin datos el día anterior (no calculable)")


def salud_tabla(conn, cfg: dict) -> None:
    """Chequeos sobre TODO el histórico: detecta duplicación de filas y montos raros."""
    _titulo("SALUD DE LA TABLA (histórico completo)")
    r = conn.execute(text(f"""
        SELECT COUNT(*) lineas, COUNT(DISTINCT {cfg['factura']}) comprobantes,
               MIN({cfg['fecha']}) desde, MAX({cfg['fecha']}) hasta
        FROM {cfg['tabla']}
    """)).mappings().first()
    print(f"Líneas totales    : {r['lineas']:,}".replace(",", "."))
    print(f"Comprobantes      : {r['comprobantes']:,}".replace(",", "."))
    print(f"Rango de fechas   : {r['desde']}  ->  {r['hasta']}")

    # comprobantes con cantidad de líneas anómala (síntoma de filas duplicadas).
    # Excluimos factura NULL: si no, todas las líneas sin comprobante se agrupan
    # en un solo "NULL" y aparecen como un falso comprobante gigante.
    umbral = 300
    anomalos = conn.execute(text(f"""
        SELECT COUNT(*) FROM (
            SELECT {cfg['factura']} FROM {cfg['tabla']}
            WHERE {cfg['factura']} IS NOT NULL
            GROUP BY {cfg['factura']} HAVING COUNT(*) > :u
        ) t
    """), {"u": umbral}).scalar()
    if anomalos:
        print(f"\n⚠  {anomalos} comprobante(s) con más de {umbral} líneas — posible "
              "duplicación de filas en la carga. Ejemplos:")
        for x in conn.execute(text(f"""
            SELECT {cfg['factura']} AS factura, COUNT(*) lineas,
                   ROUND(SUM({cfg['monto']})) total, DATE(MAX({cfg['fecha']})) fecha
            FROM {cfg['tabla']} WHERE {cfg['factura']} IS NOT NULL
            GROUP BY {cfg['factura']}
            HAVING COUNT(*) > :u ORDER BY lineas DESC LIMIT 5
        """), {"u": umbral}).mappings():
            print(f"    {str(x['factura']):<18} líneas={x['lineas']:<7} fecha={x['fecha']} "
                  f"SUM(monto) crudo={_gs(x['total'])}")
        print("    (Nota: la vista v_ventas_lineas del SQL propuesto deduplica esto por idventadet.)")
    else:
        print(f"\nSin comprobantes (no nulos) con más de {umbral} líneas. No hay señal de "
              "duplicación de filas.")

    # ventas sin número de comprobante (factura NULL): entran en SUM(monto) pero
    # no en COUNT(DISTINCT factura), así que distorsionan el ticket promedio.
    nulos = conn.execute(text(f"""
        SELECT COUNT(*) lineas, ROUND(COALESCE(SUM({cfg['monto']}), 0)) total
        FROM {cfg['tabla']} WHERE {cfg['factura']} IS NULL
    """)).mappings().first()
    if nulos["lineas"]:
        print(f"\n⚠  {nulos['lineas']:,} línea(s) con {cfg['factura']} NULL "
              f"(sin comprobante), por {_gs(nulos['total'])}.".replace(",", "."))
        print(f"   Se suman en el total pero no se cuentan como comprobante -> "
              "revisar el ticket promedio.")
    else:
        print(f"\nNo hay líneas con {cfg['factura']} NULL. Todo tiene comprobante.")

    # montos negativos / cero (anuladas o notas de crédito con signo)
    neg = conn.execute(text(f"""
        SELECT SUM({cfg['monto']} < 0) neg, SUM({cfg['monto']} = 0) cero
        FROM {cfg['tabla']}
    """)).mappings().first()
    print(f"\nLíneas con {cfg['monto']} < 0 : {neg['neg'] or 0}  (posibles notas de crédito/anuladas)")
    print(f"Líneas con {cfg['monto']} = 0 : {neg['cero'] or 0}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Validador de ventas por empresa (solo lectura).")
    parser.add_argument("--empresa", required=True, help="ejapo | ekaru (según CONFIG_POR_EMPRESA)")
    parser.add_argument("--fecha", default=None, help="Día a analizar YYYY-MM-DD (por defecto: ayer)")
    args = parser.parse_args(argv[1:])

    empresa = args.empresa.strip().lower()
    cfg = CONFIG_POR_EMPRESA.get(empresa)
    if cfg is None:
        disponibles = ", ".join(sorted(CONFIG_POR_EMPRESA)) or "(ninguna)"
        print(f"ERROR: la empresa '{empresa}' no está en CONFIG_POR_EMPRESA (core/lfl.py).")
        print(f"       Empresas configuradas y validadas: {disponibles}.")
        print("       No se valida a ciegas: primero hay que confirmar su tabla y columnas "
              "reales y agregarla a esa config.")
        return 2

    database = _database_de(empresa)
    if not database:
        print(f"ERROR: no encuentro la base de datos de '{empresa}' en la config "
              f"(esperaba settings.{empresa}_database).")
        return 2

    if args.fecha:
        try:
            dia = datetime.strptime(args.fecha, "%Y-%m-%d").date()
        except ValueError:
            print(f"ERROR: --fecha '{args.fecha}' no tiene formato YYYY-MM-DD.")
            return 2
    else:
        dia = date.today() - timedelta(days=1)

    print(f"Empresa : {empresa}")
    print(f"Base    : {database}  (usuario solo-lectura)")
    print(f"Config  : tabla={cfg['tabla']} fecha={cfg['fecha']} monto={cfg['monto']} "
          f"factura={cfg['factura']} sucursal={cfg['sucursal']}")

    engine = get_engine(database)
    with engine.connect() as conn:
        salud_tabla(conn, cfg)
        validar_dia(conn, cfg, dia)

    print("\nValidación terminada. Ninguna escritura fue realizada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
