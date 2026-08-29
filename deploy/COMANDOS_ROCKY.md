# COMANDOS — Fase 3 en Rocky Linux 8.10

Reescritura del `COMANDOS.md` original, que estaba hecho para Ubuntu con `apt`, `ufw` y
túnel Cloudflare. Nada de eso aplica acá.

**Un comando por vez.** Cada paso se verifica antes del siguiente.

**Nunca** contraseñas en línea de comando. **Nunca** secretos en `COMMANDS.md`, en el
journal ni en pantalla.

---

## 0 · Antes de tocar nada

Firebird está vivo en el puerto 3050 y **no se toca**. El landing de `kyber.com.py` sigue
publicado hasta el cutover. No reiniciar el servidor sin avisar.

```
systemctl status firebird --no-pager
ss -lntp | grep -E '3050|443|80'
df -h /
free -h
```

Espacio necesario: ~2,5 GB para las bases y ~1 GB para el venv y el código.

### Relevamiento previo (reportado por Esteban, sin verificar)

- Rocky 8.10, kernel 4.18, hostname `localhost.localdomain`
- `/` 120 GB al 6% · **`/datos` 350 GB al 2%**
- Corren `nginx` (80/443), **`php-fpm`** y Firebird en 3050
- Bases Firebird en `/datos/dbases`, UDFs en `/datos/udfs`
- Bajo `/opt/kyber` **solo existe `landing/index.html`**
- **NO instalados:** `git`, `git-lfs`, `gh`, `docker`
- Sí instalados: `node` v22.23.2, `python3` 3.6.8

Tres consecuencias sobre el plan original:

1. **Las bases van en `/datos`, no en `/`.** Hay 350 GB libres ahí contra 120 GB en la
   raíz, y Ekarú crece.
2. **Sin `git` en el servidor, el código llega por `rsync` sobre SSH**, no por `clone`.
   Es incluso preferible: el repositorio de trabajo no debe llevar el `.env`.
3. **`php-fpm` ya está corriendo**, así que hay un PHP instalado sirviendo algo. Ver el
   paso 9: cambia el procedimiento.

---

## 1 · Zona horaria

Va en **tres** lugares: sistema, MySQL y PHP. Si falta uno, la jornada de las 03:00 se
desplaza y **todos los números difieren** de los de Windows. Es el error más caro que se
puede cometer en silencio.

```
timedatectl set-timezone America/Asuncion
timedatectl
```

---

## 2 · SELinux

`getenforce` da el estado en runtime. Lo que manda después de un reinicio es el archivo.

```
getenforce
grep -E '^SELINUX=' /etc/selinux/config
```

Si el archivo dice `enforcing`, el próximo reinicio rompe el proxy de Nginx al motor.
Cuando llegue ese momento:

```
setsebool -P httpd_can_network_connect 1
```

---

## 3 · Usuario de servicio

```
useradd --system --home-dir /opt/kyber --shell /sbin/nologin kyber
mkdir -p /opt/kyber/app /opt/kyber/sync /opt/kyber/shared /opt/kyber/backups /opt/kyber/deploy /opt/kyber/handoff
mkdir -p /etc/kyber
chown -R kyber:kyber /opt/kyber
chmod 750 /etc/kyber
```

`/opt/kyber/landing` ya existe y no se toca.

---

## 4 · Python 3.12

Rocky 8 trae 3.6.8 como `python3` del sistema. **No usarlo.**

Verificado en el servidor el 27/08: `python3.12` está disponible **como paquete**, no
como módulo. `dnf module install python312` falla.

```
dnf install -y python3.12 python3.12-pip
/usr/bin/python3.12 --version
```

Versión disponible: 3.12.14-1.el8_10 desde AppStream.

Venv con **ese** binario, nunca con el default:

```
/usr/bin/python3.12 -m venv /opt/kyber/venv
/opt/kyber/venv/bin/python --version
/opt/kyber/venv/bin/pip install --upgrade pip
```

Dependencias, y después el lock real del servidor:

```
/opt/kyber/venv/bin/pip install -r /opt/kyber/deploy/requirements-lock.txt
/opt/kyber/venv/bin/pip freeze > /opt/kyber/deploy/requirements-rocky.lock
```

El origen corre Python 3.14.6 y acá va 3.12. **Verificar la equivalencia** antes de
seguir: correr `tests/` y una consulta real por empresa.

---

## 5 · MySQL 8.4

**Corregido el 27/08 tras relevar el servidor: no hace falta el repo de Oracle.**
El AppStream de Rocky 8.10 ya ofrece el stream `mysql:8.4`, que es exactamente la versión
del origen. Se evita agregar un repositorio externo, que era el riesgo de este paso.

```
dnf module list mysql
dnf module reset mysql -y
dnf module enable mysql:8.4 -y
```

**Leer el plan de transacción completo antes de confirmar.** Firebird no se toca.

```
dnf install -y mysql-server
```

### ANTES de arrancar por primera vez: `lower_case_table_names`

**Esto es lo más importante de todo el paso, y no está en ninguna documentación previa.
Se descubrió el 27/08 con el sincronizador ya corriendo.**

MySQL en Linux **distingue mayúsculas** en los nombres de tabla; en Windows no. El volcado
trae las tablas en minúscula (`ventas`) porque Windows las guarda así, pero el conector
las pide con la capitalización de `vistas.txt` (`Ventas`). En Windows son la misma tabla.
En Linux, sin este ajuste, son dos: el conector no encuentra la suya, la da por nueva,
crea una vacía y **arranca a redescargar la tabla entera**. El resultado serían 20 tablas
duplicadas por capitalización, las vistas apuntando a las viejas y el doble de datos.

**Solo se puede fijar al INICIALIZAR el datadir.** Si se cambia después, `mysqld` se niega
a arrancar y hay que reinicializar y reimportar.

```
printf '[mysqld]
lower_case_table_names = 1
' > /etc/my.cnf.d/kyber-lctn.cnf
```

Y recién ahí:

```
systemctl enable --now mysqld
mysql --version
mysql -N -B -e "SELECT @@lower_case_table_names"
```

**Tiene que devolver `1`.** Si devuelve `0`, parar acá: el datadir hay que reinicializarlo
antes de importar nada.

**Mover el datadir a `/datos` antes de importar nada.** En `/` hay 120 GB y en `/datos`
350 GB. Con el servicio detenido:

```
systemctl stop mysqld
mkdir -p /datos/mysql
rsync -av /var/lib/mysql/ /datos/mysql/
```

Después ajustar `datadir=/datos/mysql` en `/etc/my.cnf`, revisar el contexto de SELinux
(`semanage fcontext -a -t mysqld_db_t "/datos/mysql(/.*)?"` y `restorecon -Rv /datos/mysql`)
y recién ahí volver a arrancar. **Preguntar antes de hacerlo:** el servidor es producción
y `/datos` ya tiene las bases de Firebird.

Zona horaria de MySQL, el segundo de los tres lugares. La contraseña se escribe en el
prompt, nunca en la línea de comando:

```
mysql -u root -p -e "SELECT @@time_zone, @@system_time_zone, NOW();"
```

---

## 6 · Bases y usuarios

Las tres bases, y el usuario de solo lectura que **en Windows no existe** — allá el motor
consulta como `root`. Acá se crea bien desde el arranque.

```
mysql -u root -p
```

Ya dentro del prompt de MySQL:

```sql
CREATE DATABASE ekaru_gastronomia_bi CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE ejapo_sanjose_bi     CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE conepasa_auth        CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE USER 'conepasa_readonly'@'127.0.0.1' IDENTIFIED BY 'poner-clave-nueva';
GRANT SELECT ON ekaru_gastronomia_bi.* TO 'conepasa_readonly'@'127.0.0.1';
GRANT SELECT ON ejapo_sanjose_bi.*     TO 'conepasa_readonly'@'127.0.0.1';

CREATE USER 'conepasa_auth'@'127.0.0.1' IDENTIFIED BY 'poner-clave-nueva';
GRANT SELECT, INSERT, UPDATE, DELETE ON conepasa_auth.* TO 'conepasa_auth'@'127.0.0.1';

CREATE USER 'kyber_sync'@'127.0.0.1' IDENTIFIED BY 'poner-clave-nueva';
GRANT ALL PRIVILEGES ON ekaru_gastronomia_bi.* TO 'kyber_sync'@'127.0.0.1';
GRANT ALL PRIVILEGES ON ejapo_sanjose_bi.*     TO 'kyber_sync'@'127.0.0.1';

FLUSH PRIVILEGES;
```

El sincronizador PHP sí escribe en las bases de BI: por eso `kyber_sync` es distinto de
`conepasa_readonly`, que es el que usa el agente.

---

## 7 · Importar el dump

En Windows, con el motor y los workers detenidos para que la copia sea consistente:

```
mysqldump -u root -p --single-transaction --routines --triggers --events --databases ekaru_gastronomia_bi ejapo_sanjose_bi conepasa_auth > conepasa_full.sql
```

**Las tablas `bi_sync_*` van incluidas.** Son 3.753 filas y guardan los cursores, la cola
y el historial. Omitirlas obliga a recalcular desde `MAX(idpk)` y descarta el seguimiento.

Verificar el dump **antes** de subirlo:

```
grep -c 'CREATE TABLE' conepasa_full.sql
grep -c 'bi_sync' conepasa_full.sql
```

En Rocky:

```
mysql -u root -p < conepasa_full.sql
```

Comparar contra el inventario: 34 tablas en Ekarú, 40 en Ejapo, 12 en auth, y
10 + 10 + 3 vistas.

---

## 8 · Código y secretos

No hay `git` en el servidor, así que el código va por `rsync` sobre SSH desde la Zenbook.
Los excludes no son opcionales: el `.env` y el venv de Windows no deben llegar nunca.

```
rsync -av -e 'ssh -p 715' --exclude '__pycache__' --exclude '.env' --exclude 'venv' /ruta/motor/ root@186.12.177.53:/opt/kyber/app/
```

Ya en el servidor:

```
cp /opt/kyber/deploy/config.toml /opt/kyber/app/.streamlit/config.toml
cp /opt/kyber/deploy/kyber.env.plantilla /etc/kyber/kyber.env
```

Completar los valores **con las credenciales ya rotadas**, nunca con las del snapshot.

```
chown kyber:kyber /etc/kyber/kyber.env
chmod 600 /etc/kyber/kyber.env
```

Sin `CLIENTE_ID` ni las cuatro variables `AUTH_*`, **el login rechaza a todos** y el
síntoma no dice por qué.

```
sha256sum -c /opt/kyber/deploy/INVENTARIO_SHA256.txt
```

---

## 9 · PHP — se usa el 7.4 del sistema, no se instala nada

**Decidido el 27/08 con verificación en el servidor: no se instala PHP 8.3.**

El servidor tiene PHP 7.4.33 y `php-fpm` sirviendo `/opt/nginx/html28/admin-erp/`, 233 MB
de código de un ERP que la orden marca como intocable. Actualizar el PHP del sistema
podría romperlo.

Antes de decidir se comprobó que el conector corre en 7.4:

| Control | Resultado |
|---|---|
| Extensiones necesarias: `pdo_mysql`, `mysqlnd`, `mbstring`, `json`, `curl`, `openssl` | las 7 presentes |
| `php -l` sobre los 13 archivos del conector con 7.4 | **13 OK, 0 errores** |
| Funciones exclusivas de PHP 8 (`str_contains`, `match`, `?->`, enum, atributos…) | ninguna |
| Estilo del código | 116 usos de `array()` contra 1 de `[]`: escrito conservador a propósito |

El único riesgo real de bajar de 8.3 a 7.4 es que **PHP 8 cambió la comparación entre
strings y números** (`0 == "abc"` da verdadero en 7.4 y falso en 8). Si la lógica de
cursor dependiera de eso, se rompería en silencio. Se verificó:

- **cero comparaciones sueltas** en `SyncControl.php` y `ETLRunner.php`
- 27 comparaciones estrictas (`===` / `!==`)
- todo valor que viene de la base se castea con `(int)` antes de usarse
  (`SyncControl.php` líneas 186, 257, 272, 356)

El código nunca depende de la coerción, así que el cambio no lo afecta.

**Resultado: no se instala nada de PHP.** Un componente menos, cero riesgo para el ERP.

```
php --version
php -m | grep -iE 'pdo_mysql|mysqlnd|mbstring|json|curl'
```

Zona horaria de PHP, el tercero de los tres lugares. Debe decir `America/Asuncion`:

```
grep -n '^date.timezone' /etc/php.ini
```

`kyber-sync@.service` apunta a `/usr/bin/php`.

```
mkdir -p /opt/kyber/sync/ekaru /opt/kyber/sync/ejapo
```

### Dos observaciones, no bloqueantes

- **`php-fpm` está `enabled: disabled`**: corre ahora pero **no arranca solo tras un
  reinicio**. Si algo de `admin-erp` hiciera falta, el reboot del paso 14 lo dejaría
  caído. No lo toco porque es ajeno a este despliegue, pero conviene saberlo antes.
- El conector corre por CLI, no por FPM, así que no comparte proceso ni configuración con
  el ERP. Aunque `php-fpm` se caiga, la sincronización sigue.

---

## 10 · systemd

```
cp /opt/kyber/deploy/sistema/kyber-app.service /etc/systemd/system/
cp /opt/kyber/deploy/sistema/kyber-brief.service /etc/systemd/system/
cp /opt/kyber/deploy/sistema/kyber-brief.timer /etc/systemd/system/
cp '/opt/kyber/deploy/sistema/kyber-sync@.service' /etc/systemd/system/
cp '/opt/kyber/deploy/sistema/kyber-sync@.timer' /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now kyber-app
systemctl status kyber-app --no-pager
journalctl -u kyber-app -n 50 --no-pager
```

El motor tiene que escuchar **solo** en loopback:

```
ss -lntp | grep 8502
```

Si aparece `0.0.0.0:8502` en vez de `127.0.0.1:8502`, el `config.toml` no se está
leyendo. Revisar que esté en `/opt/kyber/app/.streamlit/`.

```
curl -I http://127.0.0.1:8502
```

**Los timers de sincronización NO se habilitan todavía.** Ver el paso 12.

---

## 11 · Probar sin tocar Nginx

Desde la Zenbook, con el landing intacto:

```
ssh -p 715 -L 8502:127.0.0.1:8502 usuario@186.12.177.53
```

Con ese túnel abierto, `http://localhost:8502` en el navegador. Es la aplicación real
corriendo en Rocky, con datos reales, sin exponer el 8502 ni tocar `kyber.com.py`.

Probar: login, selección de Ekarú y de Ejapo, una consulta real de cada una, un
presupuesto de Ejapo, y el panel de usuarios.

---

## 12 · Sincronizadores — el paso que puede dejar a los dos clientes sin datos

**ANTES de habilitar nada:** confirmar que la IP pública **186.12.177.53 está declarada
del lado de los dos ERP**. Un solo intento fallido de autenticación bloquea el token del
lado del proveedor, y se reactiva a mano desde ERP → gestión → menú BI. Hasta que eso
pase, Ekarú y Ejapo se quedan sin datos.

Con la confirmación en la mano, una empresa primero y se mira el resultado:

```
systemctl start kyber-sync@ekaru
journalctl -u kyber-sync@ekaru -f
```

Recién si esa corrida termina limpia:

```
systemctl start kyber-sync@ejapo
systemctl enable --now kyber-sync@ekaru.timer
systemctl enable --now kyber-sync@ejapo.timer
systemctl list-timers 'kyber-*' --no-pager
```

### Frecuencia efectiva y panel de sincronización

El timer horario no alcanza: el worker también aplica `auto_sync_hours`. Debe valer 1
en las dos empresas para sincronizar realmente cada hora:

```
grep -E '^(auto_sync_hours|resync_dias)[[:space:]]*=' /opt/kyber/sync/ekaru/config.txt /opt/kyber/sync/ejapo/config.txt
```

El cambio a `auto_sync_hours = 1` debe hacerse en una ventana acordada: provoca una
relectura móvil de cinco días por hora. No modificar `resync_dias = 5`.

Para habilitar el panel administrativo y su ejecución manual:

```
install -o kyber -g kyber -m 0750 /opt/kyber/app/sync/control.php /opt/kyber/sync/ekaru/control.php
install -o kyber -g kyber -m 0750 /opt/kyber/app/sync/control.php /opt/kyber/sync/ejapo/control.php
install -o root -g root -m 0440 /opt/kyber/app/deploy/sistema/kyber-sync-control.sudoers /etc/sudoers.d/kyber-sync-control
visudo -cf /etc/sudoers.d/kyber-sync-control
sudo -u kyber /usr/bin/php /opt/kyber/sync/ekaru/control.php status
sudo -u kyber /usr/bin/php /opt/kyber/sync/ejapo/control.php status
```

`control.php` es solo CLI y nunca se agrega a Nginx. La regla sudo permite arrancar
únicamente `kyber-sync@ekaru` y `kyber-sync@ejapo`; no concede una shell ni edición de
servicios.

---

## 13 · Resumen diario

Probarlo a mano antes de dejarlo automático:

```
sudo -u kyber /opt/kyber/venv/bin/python /opt/kyber/app/scripts/daily_brief.py
```

Si el correo llega bien y los números coinciden con los de Windows:

```
systemctl enable --now kyber-brief.timer
```

---

## 14 · Reinicio controlado

Antes de dar la Fase 3 por cerrada, confirmar que todo levanta solo. **Avisar antes de
reiniciar.**

```
systemctl reboot
```

Al volver:

```
systemctl status kyber-app --no-pager
systemctl list-timers 'kyber-*' --no-pager
systemctl status firebird --no-pager
curl -I https://kyber.com.py
```

Lo último confirma que el landing sigue publicado y que no rompimos nada de lo que ya
funcionaba.

---

## Lo que NO se hace en la Fase 3

- No se toca Nginx. El landing sigue sirviendo `kyber.com.py` hasta el cutover.
- No se expone el 8502 ni MySQL.
- No se apaga nada en Windows.
- No se ejecuta una actualización general de Rocky sin revisar antes Firebird.
- No se habilitan los timers de sincronización sin la confirmación de la IP en los ERP.
