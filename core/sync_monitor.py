"""Acceso seguro al estado y al disparo manual del sincronizador BI.

El panel no publica el antiguo ``sync.php`` ni recibe comandos libres. Todas las
operaciones pasan por ``control.php`` en modo CLI, con empresa y vista validadas,
y el worker se inicia mediante una regla sudo limitada a dos unidades systemd.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


EMPRESAS_VALIDAS = frozenset({"ekaru", "ejapo"})


class SyncMonitorError(RuntimeError):
    """Error operativo apto para mostrar en el panel de administracion."""


def _empresa_valida(company: str) -> str:
    normalizada = str(company or "").strip().lower()
    if normalizada not in EMPRESAS_VALIDAS:
        raise SyncMonitorError("Empresa de sincronización no válida.")
    return normalizada


def _rutas_control(company: str) -> tuple[str, str]:
    raiz = Path(os.getenv("KYBER_SYNC_ROOT", "/opt/kyber/sync"))
    php = os.getenv("KYBER_SYNC_PHP", "/usr/bin/php")
    control = raiz / company / "control.php"
    if not Path(php).is_file() or not control.is_file():
        raise SyncMonitorError(
            "El componente de actualización manual todavía no está conectado en este entorno."
        )
    return php, str(control)


def _ejecutar_control(company: str, *argumentos: str) -> dict[str, Any]:
    company = _empresa_valida(company)
    php, control = _rutas_control(company)
    try:
        resultado = subprocess.run(
            [php, control, *argumentos],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SyncMonitorError("No se pudo consultar el sincronizador.") from exc

    salida = (resultado.stdout or "").strip()
    if resultado.returncode != 0 or not salida:
        raise SyncMonitorError("El sincronizador no respondió correctamente.")

    try:
        payload = json.loads(salida)
    except json.JSONDecodeError as exc:
        raise SyncMonitorError("El sincronizador devolvió una respuesta inválida.") from exc

    if not isinstance(payload, dict) or not payload.get("ok"):
        mensaje = str(payload.get("error") or "Operación rechazada por el sincronizador.")
        raise SyncMonitorError(mensaje[:300])
    return payload


def obtener_estado(company: str) -> dict[str, Any]:
    """Estado por vista, cola, historial de Ventas y frecuencia efectiva."""
    return _ejecutar_control(company, "status")


def _iniciar_worker(company: str) -> None:
    company = _empresa_valida(company)
    sudo = os.getenv("KYBER_SYNC_SUDO", "/usr/bin/sudo")
    systemctl = os.getenv("KYBER_SYNC_SYSTEMCTL", "/usr/bin/systemctl")
    unidad = f"kyber-sync@{company}.service"
    try:
        resultado = subprocess.run(
            [sudo, "-n", systemctl, "start", "--no-block", unidad],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SyncMonitorError("La vista quedó en cola, pero no se pudo iniciar el worker.") from exc
    if resultado.returncode != 0:
        raise SyncMonitorError("La vista quedó en cola, pero systemd rechazó el inicio del worker.")


def sincronizar_ahora(company: str, vista: str | None = None) -> dict[str, Any]:
    """Encola una vista o todas y despierta el worker sin esperar al timer."""
    company = _empresa_valida(company)
    if vista is None:
        payload = _ejecutar_control(company, "enqueue-all")
    else:
        vista = str(vista or "").strip()
        if not vista or len(vista) > 100 or any(ord(c) < 32 for c in vista):
            raise SyncMonitorError("Vista de sincronización no válida.")
        payload = _ejecutar_control(company, "enqueue", vista)
    _iniciar_worker(company)
    return payload
