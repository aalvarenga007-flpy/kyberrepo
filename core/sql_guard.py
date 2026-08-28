"""Ejecución de SQL de solo lectura, con defensa en profundidad.

Esta app deja que Claude escriba la consulta SQL directamente (a diferencia
de app/ que usa plantillas fijas escritas a mano), así que estos controles
son la única barrera real entre una pregunta del usuario y la base de datos:

1. Solo se permite un único SELECT o WITH...SELECT por llamada.
2. Se bloquea cualquier palabra de escritura/administración en el cuerpo.
3. Se limita la cantidad de filas devueltas (SQL_MAX_ROWS).

Aun así, la recomendación en README.md de usar un usuario MySQL con permisos
SELECT únicamente sigue siendo la protección más importante: estos chequeos
son defensa en profundidad, no un reemplazo de permisos reales de base de datos.
"""

from __future__ import annotations

import re

import pandas as pd
from sqlalchemy import text

from core import permisos
from core.config import settings
from core.db import get_engine


ALLOWED_STARTS = ("SELECT", "WITH")

FORBIDDEN_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE",
    "GRANT", "REVOKE", "REPLACE", "MERGE", "CALL", "EXECUTE",
    "LOAD_FILE", "OUTFILE", "DUMPFILE",
)


class SQLGuardError(ValueError):
    """Se lanza cuando una consulta no cumple las reglas de solo lectura."""


def execute_readonly(
    database: str,
    query: str,
    rol: str = "admin",
    company: str = "",
) -> pd.DataFrame:
    """
    Ejecuta una consulta de solo lectura.

    `rol` y `company` son opcionales y por defecto no restringen nada,
    para que cualquier codigo que llame a esta funcion sin pasarlos
    (por ejemplo el resumen diario automatico, que no tiene usuario)
    siga funcionando igual que antes.
    """
    query_text = str(query).strip().lstrip("﻿​ \t\r\n")

    keyword_match = re.match(r"[A-Za-z]+", query_text)
    first_keyword = keyword_match.group(0).upper() if keyword_match else ""
    if first_keyword not in ALLOWED_STARTS:
        raise SQLGuardError("Solo se permiten consultas SELECT o WITH...SELECT.")

    stripped_query = query_text.rstrip(";")
    if ";" in stripped_query:
        raise SQLGuardError("No se permite más de una sentencia SQL por consulta.")

    upper_query = f" {stripped_query.upper()} "
    for keyword in FORBIDDEN_KEYWORDS:
        if re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", upper_query):
            raise SQLGuardError(f"La consulta contiene una operación no permitida: {keyword}.")

    # Permisos del rol: tablas prohibidas, columnas prohibidas y uso de
    # asterisco sobre tablas con columnas sensibles. Las reglas viven en
    # core/permisos.py, que es la unica fuente de verdad.
    motivo = permisos.validar_consulta(rol, company, stripped_query)
    if motivo:
        raise SQLGuardError(motivo)

    if not re.search(r"\bLIMIT\s+\d+\b", upper_query):
        stripped_query = f"{stripped_query}\nLIMIT {settings.sql_max_rows}"

    engine = get_engine(database)
    with engine.connect() as connection:
        return pd.read_sql(text(stripped_query), connection)
