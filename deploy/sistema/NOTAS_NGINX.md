# Nginx — estado actual y plan

## Lo que ya existe en el servidor

- `/etc/nginx/conf.d/kyber.conf` — sirve el landing de `https://kyber.com.py` y
  `https://www.kyber.com.py` desde `/opt/kyber/landing`.
- Certificado Let's Encrypt activo para `kyber.com.py`, renovación verificada.
- Nginx escucha en 80 y 443.
- Contenido anterior en `/opt/nginx/html28` — **no modificar**.

## Decisión (Esteban, 27/08/2026)

**El motor reemplaza al landing en la raíz.** `https://kyber.com.py` deja de mostrar la
página comercial y pasa a ser el login del motor.

Ventaja concreta: no hace falta registro DNS nuevo ni certificado nuevo. El de
`kyber.com.py` ya cubre todo. Un componente menos que instalar y renovar.

## Cuándo se aplica: en el cutover, no antes

`kyber.conf.cutover` **no se instala durante las fases 3 ni 4.** Mientras el motor no
esté validado, la raíz tiene que seguir sirviendo el landing: reemplazarlo antes deja el
sitio público caído o mostrando una aplicación sin probar, con dos clientes en producción
mirando.

Cómo se prueba mientras tanto, sin tocar nada de lo publicado:

    curl http://127.0.0.1:8502          # desde el propio servidor

    ssh -p 715 -L 8502:127.0.0.1:8502 usuario@186.12.177.53

y con ese túnel abierto, `http://localhost:8502` en la Zenbook. Se ve la aplicación real
corriendo en Rocky, con datos reales, sin exponer el puerto 8502 ni tocar el landing.

## Secuencia del cutover

1. `cp /etc/nginx/conf.d/kyber.conf /opt/kyber/backups/kyber.conf.landing`
2. `tar czf /opt/kyber/backups/landing.tgz /opt/kyber/landing`
3. Instalar `kyber.conf.cutover` como `/etc/nginx/conf.d/kyber.conf`
4. `nginx -t` — **siempre**, antes de recargar
5. `systemctl reload nginx`
6. Validar `https://kyber.com.py`: login, selección de empresa, una consulta real

**El rollback es el paso 1 y 2.** Si el motor falla en producción, se restaura el archivo
y se recarga Nginx: el landing vuelve en menos de un minuto. Por eso los respaldos van
antes que el cambio, no después.

## Dos cosas que suelen morder

- **WebSocket.** Sin `proxy_set_header Upgrade` y `Connection "upgrade"`, Streamlit carga
  la página y se queda girando para siempre, sin un solo error en el log de Nginx ni en
  el journal. Es el error más probable de toda la migración y el más difícil de
  diagnosticar, porque todo *parece* estar bien.
- **SELinux.** Hoy está en `Permissive`, pero eso es estado en runtime. Hay que leer
  `/etc/selinux/config`: si dice `enforcing`, el próximo reinicio bloquea la conexión de
  Nginx a `127.0.0.1:8502` y aparece un 502. Se arregla con
  `setsebool -P httpd_can_network_connect 1`.
