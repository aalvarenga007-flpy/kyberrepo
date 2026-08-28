# -*- coding: utf-8 -*-
"""
Conepasa AI - Cargador de cheques diferidos a MySQL
====================================================

Lee el Excel de cheques (carpeta datos/), lo limpia con las reglas ya
validadas (reusa conepasa_cheques.py) y lo vuelca como la tabla
'cheques_diferidos' dentro de la base de Ejapo. Cada vez que actualices el
Excel, volves a correr este script y la tabla se refresca (borra y recarga).

Una vez cargada la tabla, NO hay que tocar app.py ni la IA: el agente la
descubre solo con listar_tablas y responde con SQL, igual que las ventas.

  Empresa destino : Ejapo (ejapo_sanjose_bi)
  Tabla creada    : cheques_diferidos

Uso (con el venv activado, parado en la carpeta claude_engine):
    pip install pymysql            # si hace falta
    python cargar_cheques_a_mysql.py

IMPORTANTE - permisos: este script CREA e INSERTA, asi que necesita un usuario
MySQL con permiso de escritura (tipicamente 'root'). El usuario de solo lectura
'conepasa_readonly' NO sirve para cargar. El script usa, en este orden:
    1) MYSQL_ADMIN_USER / MYSQL_ADMIN_PASSWORD  (si los pones en el .env)
    2) si no, MYSQL_USER / MYSQL_PASSWORD del .env (sirve si ahi tenes root)
"""

import os
import sys
from datetime import date

import pymysql

import conepasa_cheques as cc           # reusa la limpieza del Excel
from core.config import settings        # reusa host/puerto/credenciales del .env

# --- Empresa / tabla destino ----------------------------------------------
BASE_DATOS = settings.ejapo_database    # Ejapo. Para Ekaru: settings.ekaru_database
TABLA = "cheques_diferidos"

# --- Usuario de escritura --------------------------------------------------
ADMIN_USER = os.getenv("MYSQL_ADMIN_USER", "") or settings.mysql_user
ADMIN_PASS = os.getenv("MYSQL_ADMIN_PASSWORD", "") or settings.mysql_password


DDL = f"""
CREATE TABLE {TABLA} (
  cheque_numero      BIGINT      NULL COMMENT 'Numero de cheque',
  proveedor          VARCHAR(255) NULL COMMENT 'Proveedor a quien se emitio el cheque',
  fecha_emision      DATE        NULL COMMENT 'Fecha en que se confecciono el cheque',
  fecha_vencimiento  DATE        NULL COMMENT 'Vto Cheque: fecha en que el proveedor lo va a depositar/cobrar',
  monto              BIGINT      NULL COMMENT 'Monto del cheque en guaranies',
  estatus            VARCHAR(20) NULL COMMENT 'ACREDITADO = ya se cobro/acredito; PENDIENTE = todavia no depositado',
  fecha_pago         DATE        NULL COMMENT 'Fecha en que efectivamente se acredito (solo si ACREDITADO)',
  KEY idx_estatus (estatus),
  KEY idx_vto (fecha_vencimiento),
  KEY idx_pago (fecha_pago)
)
COMMENT='Cheques diferidos emitidos a proveedores (origen: Excel de tesoreria, cuenta Atlas Gs. 1492513). REGLA: un cheque ENTRO/se acredito cuando estatus=ACREDITADO (usar fecha_pago). Un cheque esta PENDIENTE de deposito segun fecha_vencimiento.'
CHARSET=utf8mb4;
"""

INSERT = f"""
INSERT INTO {TABLA}
  (cheque_numero, proveedor, fecha_emision, fecha_vencimiento, monto, estatus, fecha_pago)
VALUES (%s, %s, %s, %s, %s, %s, %s)
"""


def _fecha(valor):
    """Timestamp de pandas -> date de Python (o None)."""
    return valor.date() if valor is not None and not _es_nulo(valor) else None


def _es_nulo(v):
    try:
        import pandas as pd
        return pd.isna(v)
    except Exception:
        return v is None


def main():
    print(f"Leyendo y limpiando el Excel...")
    df = cc.cargar_cheques()                       # limpieza validada
    print(f"  {len(df)} cheques operativos.")

    filas = []
    for r in df.itertuples():
        filas.append((
            int(r.nro) if not _es_nulo(r.nro) else None,
            None if _es_nulo(r.proveedor) else str(r.proveedor),
            _fecha(r.emision),
            _fecha(r.vto),
            int(r.monto) if not _es_nulo(r.monto) else None,
            None if _es_nulo(r.estatus) else str(r.estatus),
            _fecha(r.pago),
        ))

    print(f"Conectando a MySQL {settings.mysql_host}:{settings.mysql_port} "
          f"base '{BASE_DATOS}' como '{ADMIN_USER}'...")
    try:
        conn = pymysql.connect(
            host=settings.mysql_host,
            port=settings.mysql_port,
            user=ADMIN_USER,
            password=ADMIN_PASS,
            database=BASE_DATOS,
            charset="utf8mb4",
        )
    except pymysql.err.OperationalError as e:
        print("\nNo se pudo conectar. Revisa usuario/clave de escritura.")
        print(f"  Detalle MySQL: {e}")
        print("  Si tu MYSQL_USER es 'conepasa_readonly', agrega al .env:")
        print("    MYSQL_ADMIN_USER=root")
        print("    MYSQL_ADMIN_PASSWORD=tu_clave_de_root")
        sys.exit(1)

    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {TABLA};")
            cur.execute(DDL)
            cur.executemany(INSERT, filas)
        conn.commit()
    except pymysql.err.OperationalError as e:
        code = e.args[0] if e.args else None
        print(f"\nError de MySQL al crear/cargar la tabla: {e}")
        if code in (1142, 1044, 1045):  # permisos
            print("  Parece un problema de PERMISOS: el usuario no puede crear "
                  "tablas.\n  Corré con un usuario de escritura (root). Agregá al .env:")
            print("    MYSQL_ADMIN_USER=root")
            print("    MYSQL_ADMIN_PASSWORD=tu_clave_de_root")
        sys.exit(1)
    finally:
        conn.close()

    # Resumen de control
    pend = sum(1 for f in filas if f[5] == "PENDIENTE")
    acred = sum(1 for f in filas if f[5] == "ACREDITADO")
    print(f"\nListo. Tabla '{BASE_DATOS}.{TABLA}' creada con {len(filas)} cheques "
          f"({acred} acreditados, {pend} pendientes).")
    print("Ahora, en la plataforma con Ejapo seleccionada, ya podés preguntar por cheques.")


if __name__ == "__main__":
    main()
