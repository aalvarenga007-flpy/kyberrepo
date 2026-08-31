# Panel PHP original en pruebas

Se conserva `sync/index.php`. Un gateway dedicado limita las páginas, comprueba
el administrador en `conepasa_auth_pruebas` y valida la empresa en cada petición.
El botón sobre la versión entrega un acceso de un solo uso, válido por 5 minutos;
no viaja en parámetros de URL ni logs. La sesión PHP dura como máximo 30 minutos,
depende de la sesión de Kyber y se revoca al salir de Kyber.

## Superficies

- HTTPS de pruebas: `https://kyber.com.py:8443/` (certificado existente).
- `9029` se redirige solo después de validar HTTPS desde Internet.
- Panel: `/panel-sync/ekaru/index.php` y `/panel-sync/ejapo/index.php`.
- Solo `entrar`, `index.php`, `sync.php` y `configuracion.php` pasan al gateway.
- No se sirve `worker.php`, código fuente, configuraciones, logs ni directorios.
- FPM separado del PHP existente; no se toca el servicio php-fpm compartido.
- No se modifica el código de `/opt/kyber/sync` ni la app oficial.

## Datos reales y acciones

La pantalla lee los sincronizadores existentes. Las acciones de sincronización
actualizan las bases BI reales (también usadas por producción). El panel avisa y
solicita confirmación antes de encolar. Las mutaciones requieren POST, token CSRF,
origen HTTPS exacto, sesión válida y rol/empresa vigentes. Solo las dos unidades
preexistentes `kyber-sync@ekaru` y `kyber-sync@ejapo` se pueden despertar mediante
archivos de solicitud observados por systemd. PHP no ejecuta comandos ni usa sudo.
Las conexiones/horarios son de consulta en pruebas; no se exponen secretos ni se
ofrece el formulario antiguo que imprimía contraseñas. El worker original sigue
usando su configuración y temporizadores sin cambios.

## Instalación y validación

Empaquetar un commit, verificar SHA256, extraer a un directorio
`/opt/kyber/app-pruebas.release-<sha>` y ejecutar las pruebas Python/PHP antes de
invocar `bash deploy/panel-pruebas/deploy.sh <release>`. El script guarda respaldo,
prepara TLS sin retirar 9029 y no inicia workers. Validar el acceso TLS externo,
las denegaciones, el login y el estado por empresa antes de reemplazar la config
de pruebas por `nginx.conf` y habilitar las dos unidades `.path` y el FPM.

Conservar la carpeta de rollback y la configuración Nginx anterior. Nunca mezclar
este despliegue con `main` ni con `/opt/kyber/app` sin autorización separada.
