"""Single-use, short-lived handoff from an authenticated Kyber admin to PHP.

Tickets live outside the checkout and travel in a URL fragment, never an access
log. PHP rechecks the user/role/company in the test authentication database.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

EMPRESAS = {"ekaru", "ejapo"}
LEASE_KEY = "kyber_panel_lease"


def _directory() -> Path:
    configured = os.getenv("KYBER_PANEL_STATE", "")
    if not configured:
        raise ValueError("Panel no habilitado en este entorno")
    path = Path(configured)
    if not path.is_dir():
        raise ValueError("Panel no habilitado en este entorno")
    return path


def _write_private(path: Path, payload: dict) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(payload, output)


def crear_enlace(usuario: dict, company: str, session: dict, expires: float) -> str:
    now = int(time.time())
    if (usuario.get("rol") != "admin" or usuario.get("debe_cambiar_password")
            or company not in EMPRESAS or expires <= now):
        raise PermissionError("Acceso exclusivo para administradores")
    if usuario.get("empresas") and company not in usuario["empresas"]:
        raise PermissionError("Empresa no autorizada")
    base = os.getenv("KYBER_PANEL_URL", "").rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme != "https" or not parsed.netloc or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("El panel requiere una dirección HTTPS")
    root = _directory()
    lease = session.get(LEASE_KEY)
    if not lease or not re.fullmatch(r"[a-f0-9]{64}", lease):
        lease = secrets.token_hex(32)
        _write_private(root / "leases" / lease,
                       {"uid": int(usuario["id"]), "expires": int(expires)})
        session[LEASE_KEY] = lease
    # Keep accumulation bounded; do not read or emit expired ticket contents.
    for old in (root / "tickets").iterdir():
        if re.fullmatch(r"[a-f0-9]{64}", old.name):
            try:
                if old.stat().st_mtime < now - 600:
                    old.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
    token = secrets.token_hex(32)
    _write_private(root / "tickets" / hashlib.sha256(token.encode()).hexdigest(), {
        "uid": int(usuario["id"]), "company": company, "lease": lease,
        "expires": min(now + 300, int(expires)),
        "session_expires": min(now + 1800, int(expires)),
    })
    return f"{base}/{company}/entrar#{token}"


def revocar(session: dict) -> None:
    lease = session.pop(LEASE_KEY, None)
    if lease and re.fullmatch(r"[a-f0-9]{64}", str(lease)):
        try:
            (_directory() / "leases" / lease).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
