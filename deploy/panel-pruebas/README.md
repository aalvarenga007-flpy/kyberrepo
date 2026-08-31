# Panel PHP original en pruebas

Se conserva `sync/index.php`. Un gateway dedicado limita las páginas, comprueba
el administrador en `conepasa_auth_pruebas` y valida la empresa en cada petición.
El botón sobre la versión entrega un acceso de un solo uso, válido por 5 minutos;
no viaja en parámetros de URL ni logs. La sesión PHP dura como máximo 30 minutos,
depende de la sesión de Kyber y se revoca al salir de Kyber.

## Superficies

- HTTPS de pruebas: `https://kyber.com.py/pruebas/` (certificado existente).
- `http://186.12.177.53:9029/` redirige a esa entrada; no acepta contraseñas por HTTP.
- Panel: `/pruebas/panel-sync/ekaru/index.php` y la variante `ejapo`.
- Se agrega un include de ubicaciones `/pruebas/` al vhost HTTPS existente;
  la ruta `/` y su upstream de producción permanecen sin cambios.
- No requiere abrir 8443 ni modificar el router o firewall.
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
invocar `bash deploy/panel-pruebas/deploy.sh <release>`. Este script es la migración
única desde el estado inspeccionado de 9029; no es un actualizador genérico.
Guarda respaldo de todas las configuraciones que modifica, comprueba el hash
del vhost antes de insertar solo el include y revierte ante fallas de validación.
Comprueba acceso, cookies, retorno a pruebas, denegaciones y estado por empresa.
No inicia workers. Luego de validar HTTPS externo, habilitar el FPM y las dos
unidades `.path` existentes. No volver a escribir los archivos de solicitud al
habilitarlas. Una publicación futura debe preservar el esquema y los datos,
y actualizar solo el release de pruebas con autorización.

Conservar la carpeta de rollback y la configuración Nginx anterior. Nunca mezclar
este despliegue con `main` ni con `/opt/kyber/app` sin autorización separada.
