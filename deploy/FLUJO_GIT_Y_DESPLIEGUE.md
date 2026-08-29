# Flujo de trabajo y despliegue de Kyber

## Fuente única

El repositorio GitHub `aalvarenga007-flpy/kyberrepo` es la fuente única. OneDrive y el
snapshot histórico de Windows no se usan para desarrollar ni desplegar.

Cada PC tiene su propio clon y su propia identidad Git:

```powershell
git config user.name "Nombre Apellido"
git config user.email "correo-de-la-cuenta-github"
git pull --ff-only origin main
git switch -c cambio/descripcion-corta
```

Después de probar:

```powershell
git add -A
git commit -m "Descripción concreta del cambio"
git push -u origin cambio/descripcion-corta
```

Se revisa y fusiona la rama en GitHub. Producción nunca se edita a mano y nunca recibe
un directorio sin conocer su commit.

## Identidad del despliegue

La versión funcional vive en `core/version.py`. Además, cada despliegue debe registrar
el SHA completo de Git en `/opt/kyber/app/REVISION`. Así la pantalla muestra la versión
humana (`v1.0.2`) y el servidor conserva el commit exacto para auditoría y rollback.

## Orden obligatorio

1. `git pull --ff-only` en la PC de desarrollo.
2. Rama, cambio, pruebas, commit y push con el autor real.
3. Desplegar ese SHA al entorno de pruebas `:9029`.
4. Validar login, las dos empresas, panel de sincronización y una consulta real.
5. Copiar exactamente el mismo SHA a producción y reiniciar `kyber-app`.
6. Verificar HTTPS, versión visible y logs; conservar el SHA anterior para rollback.

El servidor todavía no tiene Git instalado y la copia actual no contiene `.git`. Antes
de automatizar el paso 3 hay que instalar Git y crear un clon de solo despliegue en
`/opt/kyber/repo`; no se debe convertir `/opt/kyber/app` directamente en un checkout.
