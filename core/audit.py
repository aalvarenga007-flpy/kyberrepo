"""Auditoría propia de esta plataforma: no comparte almacenamiento con app/.

Guarda cada pregunta, el SQL que Claude ejecutó y cuántas filas devolvió,
para poder revisar después exactamente cómo se llegó a cada respuesta.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parents[1] / "logs" / "app.log"
AUDIT_DB = Path(__file__).resolve().parents[1] / "data" / "auditoria.sqlite3"


def write_log(level: str, message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as file:
        file.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [{level.upper()}] {message}\n")


def _connection() -> sqlite3.Connection:
    AUDIT_DB.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(AUDIT_DB)
    connection.execute(
        """CREATE TABLE IF NOT EXISTS consulta (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            company TEXT NOT NULL,
            question TEXT NOT NULL,
            sql_ejecutado TEXT,
            filas INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            usuario TEXT
        )"""
    )
    _migrar(connection)
    return connection


def _migrar(connection: sqlite3.Connection) -> None:
    """
    Agrega columnas nuevas a una base de auditoria ya existente.

    La tabla original no tenia la columna `usuario`. Un CREATE TABLE IF
    NOT EXISTS no la agrega a una base que ya estaba creada, asi que hay
    que hacerlo con ALTER. Las consultas viejas quedan con usuario NULL,
    que es correcto: en ese momento no habia usuarios en el sistema.

    Es seguro ejecutarlo siempre: primero verifica si la columna existe.
    """
    columnas = {
        fila[1] for fila in connection.execute("PRAGMA table_info(consulta)").fetchall()
    }
    if "usuario" not in columnas:
        connection.execute("ALTER TABLE consulta ADD COLUMN usuario TEXT")


def log_query(
    company: str,
    question: str,
    sql_statements: list[str],
    rows: int,
    error: str | None = None,
    usuario: str | None = None,
) -> None:
    """
    Registra una consulta.

    `usuario` es el nombre de acceso de quien preguntó. Queda en None
    para los procesos automáticos sin persona detrás, como el resumen
    diario: en el historial se muestran como "sistema".
    """
    with _connection() as connection:
        connection.execute(
            "INSERT INTO consulta "
            "(created_at, company, question, sql_ejecutado, filas, error, usuario) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now().isoformat(timespec="seconds"),
                company,
                question.strip(),
                "\n---\n".join(sql_statements) if sql_statements else None,
                int(rows),
                error,
                (usuario or None),
            ),
        )


def recent_queries(
    company: str, limit: int = 20, usuario: str | None = None
) -> list[tuple]:
    """
    Últimas consultas de una empresa.

    Devuelve (created_at, question, filas, error, usuario).

    Si se pasa `usuario`, trae solamente las de esa persona. Sirve para
    que un perfil no técnico pueda ver su propio historial sin ver el
    de los demás.
    """
    with _connection() as connection:
        if usuario:
            return connection.execute(
                "SELECT created_at, question, filas, error, usuario FROM consulta "
                "WHERE company = ? AND usuario = ? ORDER BY id DESC LIMIT ?",
                (company, usuario, int(limit)),
            ).fetchall()
        return connection.execute(
            "SELECT created_at, question, filas, error, usuario FROM consulta "
            "WHERE company = ? ORDER BY id DESC LIMIT ?",
            (company, int(limit)),
        ).fetchall()
