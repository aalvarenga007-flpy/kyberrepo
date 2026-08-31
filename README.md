# Conepasa IA — motor

Motor de análisis en lenguaje natural sobre las bases de BI de Ekarú Gastronomía y
Ejapo Comercial San José.

**En producción:** https://kyber.com.py — servidor Rocky Linux 8.10.
**Entorno de pruebas:** http://186.12.177.53:9029

> Este es el **repositorio de trabajo**. No contiene secretos, ni el entorno virtual, ni
> datos. Hay otro repositorio aparte (`conepasa-snapshot-windows`) que es un snapshot
> histórico del origen Windows: **ese no se usa para desarrollar.**

---

## Arquitectura, en una línea

**Un solo proceso Python/Streamlit sirve a las dos empresas.** La empresa activa sale de
la sesión del usuario (`core/auth.py → filtrar_empresas`, contra la base `conepasa_auth`),
**no** del `.env`. Por lo tanto: un servicio, un puerto, un `CLIENTE_ID`.

Si encontrás documentación que diga "un proceso por empresa", está vieja. Montar dos
partiría el control de consumo, que se lleva por empresa activa de la sesión.

```
app.py                  entrypoint Streamlit
ai/agent.py             agente, BUSINESS_NOTES, ruteo de modelos, bucle agéntico
ai/tools.py             herramientas que ejecuta el agente
core/                   15 módulos: config, db, auth, permisos, sql_guard, audit,
                        consumo, suscripcion, lfl, verificacion, estilos, formato…
scripts/daily_brief.py  resumen diario por correo
scripts/gestionar_usuarios.py   alta, baja y reseteo de contraseñas
sync/                   conector PHP de sincronización con los ERP
deploy/                 unidades systemd, Nginx y runbook del servidor
```

---

## Levantar el entorno de desarrollo

### 1. Clonar e instalar

```bash
git clone <url-del-repo> conepasa-engine
```

```bash
python -m venv venv
```

En Windows `venv\Scripts\activate`, en Linux o Mac `source venv/bin/activate`. Después:

```bash
pip install -r deploy/requirements-lock.txt
```

Python 3.12 o superior. En el servidor corre 3.12.14; en Windows funciona con 3.14.6.

### 2. Túnel a la base del servidor

MySQL del servidor escucha **solo en loopback** y así debe seguir. Para consultar los
datos reales desde tu máquina, abrí un túnel y dejá la ventana corriendo:

```bash
ssh -i ~/.ssh/kyber_ed25519 -p 715 -L 3306:127.0.0.1:3306 root@186.12.177.53
```

Con eso, `127.0.0.1:3306` en tu máquina *es* la base del servidor.

### 3. Configurar el `.env`

```bash
cp .env.example .env
```

Completá `MYSQL_PASSWORD`, `AUTH_MYSQL_PASSWORD` y `ANTHROPIC_API_KEY`. Las dos primeras
están en `/etc/kyber/kyber.env` del servidor; se leen como root.

**Dejá `AUTH_DATABASE=conepasa_auth_pruebas`.** Así tus logins y tu consumo no se mezclan
con los de Ekarú y Ejapo.

**Dejá `ALERT_SEND_EMAIL=false`.** Si lo ponés en `true`, el resumen diario manda correos
reales a cinco destinatarios.

### 4. Correr

```bash
streamlit run app.py
```

---

## Cómo se despliega

No hay despliegue automático: es deliberado, porque hay dos clientes en producción.

**Primero a pruebas**: `http://186.12.177.53:9029/` redirige a
`https://kyber.com.py/pruebas/`. Ingresar como administrador, elegir la empresa y
usar **Panel de sincronización**, encima de la versión. El panel original abre
en otra pestaña. Ver el estado no sincroniza; los botones Sync/Sincronizar todo
sí actualizan las bases BI reales, previa confirmación.

Publicar un commit exacto desde una rama de trabajo, conservando los `.env`, la
configuración de Streamlit y la carpeta anterior para reversión. No copiar una
carpeta de trabajo indiscriminadamente sobre el servicio activo. La instalación
de este acceso y sus validaciones están en `deploy/panel-pruebas/README.md`.

**Recién validado y con autorización explícita**, promover el mismo commit a
producción. La app oficial sigue en `https://kyber.com.py/` y no se actualiza por
hacer push a GitHub. Ver `CHANGELOG.md` para la política de versiones.

---

## Lo que hay que saber antes de tocar el código

Esto no se deduce leyendo los archivos y cuesta caro descubrirlo solo.

**Las ventas se consultan siempre desde `v_ventas_jornada`**, nunca desde la tabla
`ventas` cruda. La jornada corta a las 03:00: un turno de viernes que cierra a las 02:07
del sábado pertenece al viernes.

**Las tablas crudas de deudas están prohibidas** en las consultas del agente.
`deudas_de_clientes` estaba inflada ~7,8x y `deudas_con_proveedores` ~1,1x. Usar siempre
`v_deudas_clientes` y `v_deudas_proveedores`, que están deduplicadas.

**Columnas de Ekarú:** `Fecha_Hora` (no `fecha`), `subtotal` (no `total_venta`, que es el
total de factura repetido en cada línea). `Operador` es el mozo, `Cajero` el cajero.
Los tickets se cuentan con `COUNT(DISTINCT Factura)`; para Comedor hay que usar `idventa`
porque las remisiones no tienen número de factura.

**En `ordenes_pago_formas_de_pago`**, las pagadas son `pagado_el IS NOT NULL`. Filtrar por
`pagado_el`, **no** por `fecha_ordenpago`, que tiene valores corruptos.

**`daily_brief.py` hace dos llamadas al agente** (`BRIEF_VENTAS` + `BRIEF_FINANZAS`)
porque las nueve secciones y las cinco sucursales de Ekarú excedían `MAX_TOOL_ROUNDS = 14`
en una sola. **No volver a unirlas.**

**MySQL en Linux distingue mayúsculas** en los nombres de tabla y en Windows no. El
servidor corre con `lower_case_table_names = 1` para igualar el comportamiento. Sin eso,
el conector busca `Ventas`, encuentra `ventas`, las trata como distintas y redescarga
todo. Solo se puede fijar al inicializar el directorio de datos de MySQL.

**Windows corre con `sql_mode` vacío y el servidor no.** Eso ocultaba que los números de
factura de Ejapo se guardaban truncados a un dígito. Si aparece un error de
`Data truncated`, es un tipo de columna mal definido, no un problema del conector.

---

## El conector de sincronización

`sync/` es un cliente de API REST contra los ERP, que son SaaS. No se instala nada en la
red de las empresas. El `src/` es **idéntico entre Ekarú y Ejapo**: lo único que cambia es
`config.txt`, y por eso acá va uno solo, con dos ejemplos en `sync/ejemplos/`.

Descarga a una tabla `*_staging` y hace merge por upsert al final, así que un corte nunca
deja la tabla real a medias.

**Ventana móvil:** `resync_dias = 5` retrocede el cursor al primer ID de los últimos 5
días en vez de pararlo en el máximo. Eso recupera las correcciones y los comprobantes de
mesas abiertas que la paginación por ID se saltea. Si aparecen ventas faltantes, el primer
lugar a mirar es este parámetro — pero no subirlo sin entender por qué.

En el servidor corre por `systemd`, cada hora, con las unidades de `deploy/sistema/`.
Para que la frecuencia sea realmente horaria deben coincidir **dos controles**:
`OnCalendar=hourly` en el timer y `auto_sync_hours = 1` en cada `config.txt`. Con el
valor 24 el timer se despierta cada hora, pero durante 23 ejecuciones no sincroniza.

La pestaña **Sincronización**, visible solo para administradores, muestra el último
resultado por vista, cola e historial de Ventas. La acción manual usa `sync/control.php`
por CLI; ese archivo devuelve estado seguro, encola sin duplicar trabajos y no se
publica por Nginx.

El flujo de ramas, autoría, versiones y despliegue por commit está documentado en
`deploy/FLUJO_GIT_Y_DESPLIEGUE.md`.

---

## Pendientes conocidos

- **No hay respaldos automáticos** configurados en el servidor.
- `BUSINESS_NOTES` está incrustado en `ai/agent.py`. Para escalar a más clientes hay que
  sacarlo a un archivo por cliente cargado por `CLIENTE_ID`.
- El SMTP usa una cuenta personal de Gmail. Antes del primer cliente ajeno hace falta uno
  propio con SPF, DKIM y DMARC.
- La sesión se pierde al refrescar el navegador; necesita una cookie firmada.
- En Ekarú, las vistas `Compras` y `Compras y Gastos` no aplican la ventana móvil porque
  `bi_sync_control` guarda `idfactura` y la columna real es `id_factura`.
- SSH del servidor admite `root` con contraseña. Conviene pasar a usuario no-root con
  clave.
