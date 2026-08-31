# Promoción v1.0.4

`promote_release.py` hace la promoción controlada desde el estado inspeccionado
el 31/08/2026: primero `test`, después `production` con el mismo commit. No es
un instalador general ni cambia MySQL, Firebird, temporizadores o destinatarios.

- Oficial: `https://kyber.com.py/`, `/opt/kyber/app`, `conepasa_auth`.
- Pruebas: `https://kyber.com.py/pruebas/`, `/opt/kyber/app-pruebas`,
  `conepasa_auth_pruebas`; la entrada 9029 sigue redirigiendo ahí.
- Cada panel tiene FPM, socket, tickets, sesiones y login independientes.
- Comparten los dos workers BI existentes. Las acciones actualizan datos reales.
- Ambos siguen comprobando rol/empresa y CSRF en cada acción.

Empaquetar un commit limpio sin secretos, extraerlo a `/opt/kyber/release-<sha>`
y escribir su SHA completo en `REVISION`. Ejecutar pruebas Python, PHP, JS y
`scripts/test_wake_burst.py` (usa workers simulados, no realiza sincronizaciones).
Invocar con `/opt/kyber/venv/bin/python deploy/promote_release.py test <release>`;
validar y repetir con `production`. El script crea respaldos de configuración,
conserva el release anterior y revierte si fallan los controles internos.

**No borrar las carpetas de rollback:** `data` y `logs` se conservan y enlazan
desde el nuevo release para no perder historial ni escrituras al revertir.
Una futura limpieza requiere migrar ese estado persistente de forma coordinada.
Las claves siguen fuera de Git, en los EnvironmentFile preexistentes.

Las conexiones y horarios del panel son de consulta; no se exponen ni editan
claves. La versión del panel se obtiene de `core/version.py`.
