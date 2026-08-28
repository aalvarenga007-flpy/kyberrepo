-- =====================================================================
-- Vistas fijas de ventas para Conepasa AI
-- =====================================================================
-- Objetivo: fijar en la base la definición de "una línea", "una venta" y
-- "el total del día", para que ni el modelo ni un reporte tengan que
-- reinterpretarla en cada consulta. Esto elimina de raíz la intermitencia
-- que causó el resumen inflado del 04/08/2026.
--
-- Estas vistas son robustas ante los DOS problemas de datos detectados y
-- verificados el 04/08/2026 contra datos reales:
--
--   (1) DUPLICACIÓN CABECERA×DETALLE: la columna `total_venta` NO es el monto
--       de la línea; es el total del comprobante ENTERO repetido en cada
--       línea. Sumar `total_venta` por línea infla 20-50x. El monto real por
--       línea es `subtotal` (= Precio_Unitario * cantidad). -> estas vistas
--       nunca suman `total_venta`; suman `subtotal`.
--
--   (2) DUPLICACIÓN DE FILAS EN LA CARGA (solo Ejapo, jul y sep 2025): en 336
--       comprobantes viejos, la MISMA línea (mismo `idventadet`) quedó
--       insertada miles de veces por una sync inicial defectuosa. Eso infla
--       `SUM(subtotal)` crudo ~36% en cualquier rango que toque 2025. -> la
--       vista base `v_ventas_lineas` colapsa esas filas agrupando por
--       `idventadet` (identificador de línea único y global; verificado: nunca
--       NULL, nunca compartido entre comprobantes). Deduplicar es inofensivo
--       donde no hay duplicados (Ekarú: 0 filas duplicadas).
--
-- Grano de comprobante (asimétrico y confirmado por empresa):
--   * Ejapo: `idventa`  (Factura viene con muchos NULL -> no sirve para contar)
--   * Ekarú: `Factura`  (en Ekarú idventa NO es 1:1 con la factura -> infla)
--
-- CÓMO SE APLICA (requiere usuario admin, NO el read-only):
--   conepasa_readonly no puede crear vistas. Correr como admin (ver
--   MYSQL_ADMIN_USER / MYSQL_ADMIN_PASSWORD en .env) y luego dar SELECT:
--       GRANT SELECT ON ejapo_sanjose_bi.v_ventas_lineas   TO 'conepasa_readonly'@'%';
--       GRANT SELECT ON ejapo_sanjose_bi.v_ventas          TO 'conepasa_readonly'@'%';
--       GRANT SELECT ON ejapo_sanjose_bi.v_ventas_diarias  TO 'conepasa_readonly'@'%';
--       GRANT SELECT ON ekaru_gastronomia_bi.v_ventas_lineas  TO 'conepasa_readonly'@'%';
--       GRANT SELECT ON ekaru_gastronomia_bi.v_ventas          TO 'conepasa_readonly'@'%';
--       GRANT SELECT ON ekaru_gastronomia_bi.v_ventas_diarias  TO 'conepasa_readonly'@'%';
--
-- Idempotente: CREATE OR REPLACE, se puede volver a correr sin romper nada.
-- =====================================================================


-- =====================================================================
-- EJAPO COMERCIAL SAN JOSÉ  (base: ejapo_sanjose_bi)
-- =====================================================================

-- (base) Una fila por LÍNEA real de venta. Colapsa la duplicación de filas
-- agrupando por idventadet. subtotal/costo quedan al valor real de la línea.
CREATE OR REPLACE VIEW ejapo_sanjose_bi.v_ventas_lineas AS
SELECT
    idventadet                    AS idventadet,
    MAX(idventa)                  AS idventa,
    MAX(Factura)                  AS factura,
    MAX(Fecha_Hora)               AS fecha_hora,
    DATE(MAX(Fecha_Hora))         AS fecha,
    MAX(Sucursal)                 AS sucursal,
    MAX(Condicion_Venta)          AS condicion_venta,
    MAX(Categoria)                AS categoria,
    MAX(Sub_Categoria)            AS sub_categoria,
    MAX(Producto)                 AS producto,
    MAX(idcliente)                AS idcliente,
    MAX(razon_social)             AS razon_social,
    MAX(ruc)                      AS ruc,
    MAX(cantidad)                 AS cantidad,
    MAX(subtotal)                 AS subtotal,
    MAX(subtotal_costo)           AS subtotal_costo
FROM ejapo_sanjose_bi.ventas
GROUP BY idventadet;

-- Una fila por COMPROBANTE (define "una venta"). total_venta = SUM(subtotal)
-- de sus líneas reales, que coincide con el total de cabecera verificado.
CREATE OR REPLACE VIEW ejapo_sanjose_bi.v_ventas AS
SELECT
    idventa                                    AS idventa,
    MAX(fecha_hora)                            AS fecha_hora,
    DATE(MAX(fecha_hora))                      AS fecha,
    MAX(sucursal)                              AS sucursal,
    MAX(condicion_venta)                       AS condicion_venta,
    MAX(idcliente)                             AS idcliente,
    MAX(razon_social)                          AS razon_social,
    MAX(ruc)                                   AS ruc,
    COUNT(*)                                   AS lineas,
    ROUND(SUM(subtotal))                       AS total_venta,
    ROUND(SUM(subtotal_costo))                 AS total_costo,
    ROUND(SUM(subtotal) - SUM(subtotal_costo)) AS margen
FROM ejapo_sanjose_bi.v_ventas_lineas
GROUP BY idventa;

-- Una fila por DÍA y SUCURSAL (define "el total del día").
CREATE OR REPLACE VIEW ejapo_sanjose_bi.v_ventas_diarias AS
SELECT
    fecha                                              AS fecha,
    sucursal                                           AS sucursal,
    ROUND(SUM(subtotal))                               AS total_ventas,
    COUNT(DISTINCT idventa)                            AS comprobantes,
    ROUND(SUM(subtotal) / NULLIF(COUNT(DISTINCT idventa), 0)) AS ticket_promedio
FROM ejapo_sanjose_bi.v_ventas_lineas
GROUP BY fecha, sucursal;


-- =====================================================================
-- EKARÚ GASTRONOMÍA  (base: ekaru_gastronomia_bi)
-- Misma estructura; grano de comprobante = Factura (lógica validada en
-- producción: core/lfl.py y BUSINESS_NOTES["ekaru"]). Ekarú hoy no tiene
-- filas duplicadas, así que la vista base es una identidad, pero deja la
-- misma protección puesta por si una sync futura las introduce.
-- =====================================================================

CREATE OR REPLACE VIEW ekaru_gastronomia_bi.v_ventas_lineas AS
SELECT
    idventadet                    AS idventadet,
    MAX(idventa)                  AS idventa,
    MAX(Factura)                  AS factura,
    MAX(Fecha_Hora)               AS fecha_hora,
    DATE(MAX(Fecha_Hora))         AS fecha,
    MAX(Sucursal)                 AS sucursal,
    MAX(Condicion_Venta)          AS condicion_venta,
    MAX(Categoria)                AS categoria,
    MAX(Sub_Categoria)            AS sub_categoria,
    MAX(Producto)                 AS producto,
    MAX(idcliente)                AS idcliente,
    MAX(razon_social)             AS razon_social,
    MAX(ruc)                      AS ruc,
    MAX(cantidad)                 AS cantidad,
    MAX(subtotal)                 AS subtotal,
    MAX(subtotal_costo)           AS subtotal_costo
FROM ekaru_gastronomia_bi.ventas
GROUP BY idventadet;

CREATE OR REPLACE VIEW ekaru_gastronomia_bi.v_ventas AS
SELECT
    factura                                    AS factura,
    MAX(fecha_hora)                            AS fecha_hora,
    DATE(MAX(fecha_hora))                      AS fecha,
    MAX(sucursal)                              AS sucursal,
    MAX(condicion_venta)                       AS condicion_venta,
    MAX(razon_social)                          AS razon_social,
    COUNT(*)                                   AS lineas,
    ROUND(SUM(subtotal))                       AS total_venta,
    ROUND(SUM(subtotal_costo))                 AS total_costo,
    ROUND(SUM(subtotal) - SUM(subtotal_costo)) AS margen
FROM ekaru_gastronomia_bi.v_ventas_lineas
WHERE factura IS NOT NULL
GROUP BY factura;

CREATE OR REPLACE VIEW ekaru_gastronomia_bi.v_ventas_diarias AS
SELECT
    fecha                                              AS fecha,
    sucursal                                           AS sucursal,
    ROUND(SUM(subtotal))                               AS total_ventas,
    COUNT(DISTINCT factura)                            AS comprobantes,
    ROUND(SUM(subtotal) / NULLIF(COUNT(DISTINCT factura), 0)) AS ticket_promedio
FROM ekaru_gastronomia_bi.v_ventas_lineas
GROUP BY fecha, sucursal;
