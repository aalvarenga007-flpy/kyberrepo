# MANIFIESTO DE DESPLIEGUE — Fase 2

Preparado 27/08/2026. Todo generado localmente: **no se tocó el servidor ni producción**.

Este paquete es lo que se sube a `/opt/kyber`. Reemplaza al `COMANDOS.md` original, que
estaba escrito para Ubuntu con túnel Cloudflare y no sirve tal cual en Rocky.

---

## Decisiones tomadas (27/08/2026)

| Decisión | Elegido | Consecuencia |
|---|---|---|
| Python en Rocky | **3.12 de AppStream** | Difiere del 3.14.6 del origen. Hay que correr `tests/` y una consulta real de cada empresa antes de darlo por equivalente |
| MySQL | **8.4 del repo de Oracle** | Misma versión que el origen: el dump de 2,1 GB restaura sin sorpresas. Repo externo: revisar el plan de transacción de dnf para no tocar Firebird |
| Ventana de sincronización | **Cada hora, 24/7** (como hoy) | No se implementa la restricción 17:00–03:00. Se apoya en la ventana móvil de 5 días, ya activa |
| Túnel Cloudflare | **Se abandona** | Nginx directo. La IP 186.12.177.53 queda expuesta y se pierde la capa de WAF/DDoS |

---

## Contenido del paquete

| Archivo | Destino en el servidor |
|---|---|
| `requirements-lock.txt` | insumo de `pip install`, no se copia |
| `kyber.env.plantilla` | `/etc/kyber/kyber.env` — **permisos 600, dueño `kyber`** |
| `config.toml` | `/opt/kyber/app/.streamlit/config.toml` |
| `sistema/kyber-app.service` | `/etc/systemd/system/` |
| `sistema/kyber-sync@.service` y `.timer` | `/etc/systemd/system/` |
| `sistema/kyber-brief.service` y `.timer` | `/etc/systemd/system/` |
| `sistema/kyber.conf.cutover` | `/etc/nginx/conf.d/kyber.conf` — **SOLO en el cutover, no en la Fase 3** |
| `INVENTARIO_SHA256.txt` | referencia para verificar la copia |

---

## Qué se copia y qué no

### Se copia — 49 archivos con hash en `INVENTARIO_SHA256.txt`

**Motor**, siempre desde `claude_engine\`: `app.py` · `ai/` · `core/` (15 módulos) ·
`scripts/` · los 7 módulos sueltos de la raíz · `.streamlit/config.toml` · `tests/` ·
`sql/vistas_ventas.sql`.

**Conector PHP**: `src/` **una sola vez** —verificado byte por byte idéntico entre
`bi_ekaru` y `bi_ejapo`, también después del parche— más `worker.php`, `sync.php`,
`index.php`, `configuracion.php`, `vistas.txt`, y los **dos** `config.txt`.

**Datos**: `datos/CHEQUES DIFERIDOS.xlsx` (insumo del módulo de cheques).

### No se copia

`.env` · `copia.env` · `venv/` · `__pycache__/` · `cloudflared.exe` · `files/` y
`files.zip` · `data/alertas/*.html` · `data/auditoria.sqlite3` · `.claude/` ·
`Cuestionario_Presupuestador_Ejapo.xlsx` · los **11 `.bat`** · README ·
`CONTEXTO_VALIDACION.md` · `src/backup_20260824`.

### Trampa de duplicados

`app.py`, `core/config.py`, `core/db.py` y `core/audit.py` existen con el mismo nombre y
distinto contenido en `claude_engine\` y en `app\`. Los de `app\` son restos viejos
(`app.py`: 27.512 B contra 1.168 B). **Siempre los de `claude_engine\`.** Si el
inventario SHA256 muestra dos homónimos, este es el motivo.

---

## Estructura objetivo

```
/opt/kyber/
├── app/            código del motor
├── sync/
│   ├── ekaru/      src/ + worker.php + config.txt de Ekarú
│   └── ejapo/      src/ + worker.php + config.txt de Ejapo
├── venv/           creado en Rocky con python3.12, NUNCA copiado de Windows
├── shared/         data/, uploads/, templates/
├── backups/
├── deploy/
├── handoff/        STATUS.md, events.jsonl, INVENTORY.md, COMMANDS.md, VALIDATION.md
└── landing/        YA EXISTE. Se respalda y se retira en el cutover (es el rollback)

/etc/kyber/kyber.env      secretos, 600, fuera de Git
```

---

## Orden de instalación (Fase 3)

Un comando por vez. Cada paso se verifica antes del siguiente.

1. **Zona horaria del sistema**: `timedatectl set-timezone America/Asuncion`.
   Va en tres lugares —sistema, MySQL y PHP—. Si falta uno, la jornada de las 03:00
   se desplaza y **todos los números difieren**.
2. **Verificar SELinux**: `getenforce` da el estado en runtime, pero hay que leer
   `/etc/selinux/config`. Si el archivo dice `enforcing`, el próximo reinicio rompe todo.
3. Usuario de servicio `kyber`, sin login.
4. `dnf module install python312`, venv con `/usr/bin/python3.12 -m venv /opt/kyber/venv`.
5. `pip install -r requirements-lock.txt`, después `pip freeze > requirements-rocky.lock`.
6. MySQL 8.4 desde el repo de Oracle. **Revisar el plan de transacción de dnf antes de
   confirmar: Firebird está vivo en 3050 y no se toca.**
7. Crear las tres bases y el usuario `conepasa_readonly`, que **hoy no existe** — en
   Windows el motor consulta como `root`.
8. Importar el dump. **Con las tablas `bi_sync_*` incluidas** (3.753 filas: guardan los
   cursores, la cola y el historial).
9. Copiar el código y los conectores. `.env` a `/etc/kyber/kyber.env` con permisos 600.
10. PHP 8.3 CLI desde Remi. `dnf module reset php` puede tocar paquetes existentes:
    revisar la transacción antes de aceptar.
11. Unidades de systemd. `daemon-reload`, `enable --now kyber-app`.
12. **Probar con `curl http://127.0.0.1:8502` desde el servidor.**
13. **Nginx no se toca en la Fase 3.** El landing sigue sirviendo `kyber.com.py` hasta el
    cutover. La validación de la Fase 4 se hace por túnel SSH:
    `ssh -p 715 -L 8502:127.0.0.1:8502 usuario@186.12.177.53` y después
    `http://localhost:8502` en la Zenbook.
14. **No habilitar los timers de sincronización** hasta confirmar que la IP
    186.12.177.53 está declarada del lado de los dos ERP.

---

## Defectos del origen que este paquete corrige

| Defecto | Dónde | Corrección |
|---|---|---|
| `pdfplumber` **no declarado** en `requirements.txt`, importado sin `try/except` en `presupuestador.py:329` y `:567` | origen | agregado a `requirements-lock.txt` |
| Todas las dependencias con `>=`, sin fijar | origen | versiones exactas |
| `showErrorDetails = "full"` | `.streamlit/config.toml` | `"minimal"` |
| `address = "0.0.0.0"` (expuesto a la LAN) | `.streamlit/config.toml` | `127.0.0.1`, detrás de Nginx |
| `ALERT_SHOW_WINDOW=true` (falla sin pantalla) | `.env` | `false` en la plantilla |
| El motor consulta como `root` | `.env` | `conepasa_readonly` en la plantilla, a crear en el paso 7 |
| Los servicios dependen de la sesión de Windows iniciada | tareas programadas | systemd, sin sesión |
| **MySQL en Linux distingue mayúsculas en nombres de tabla** — el conector pide `Ventas` y el volcado trae `ventas` | diferencia de plataforma | `lower_case_table_names = 1`, fijado **antes** de inicializar el datadir |

---

## Lo que este paquete NO resuelve

- **Acceso SSH.** No hay ninguna clave en la Zenbook. Hay que armar el acceso a
  `186.12.177.53:715` y pasar de root con contraseña a usuario no-root con sudo y
  autenticación por clave.
- **IP declarada en los ERP.** Sin confirmarlo, la primera corrida bloquea los tokens.
- **La rotación de credenciales de la Fase S5**, que sigue pendiente.
- **El landing de `kyber.com.py` desaparece en el cutover.** Decidido el 27/08. Respaldarlo antes: es el rollback. Ver `sistema/NOTAS_NGINX.md`.
- Las ocho tablas sin columna de fecha, donde la ventana móvil no hace nada. Ver el
  punto 9.1 de `INVENTORY.md`.
