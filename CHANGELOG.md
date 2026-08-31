# Cambios publicados

## 1.0.3 — Pruebas

- Acceso para administradores al panel PHP original de sincronización, encima
  de la versión, para la empresa seleccionada.
- Pruebas por HTTPS en `/pruebas/`; el enlace del puerto 9029 redirige allí.
- Estado, vistas, historial y acciones manuales con confirmación y permisos.
- Las acciones actualizan los datos BI reales; la configuración sensible sigue
  siendo de consulta. No se cambia la aplicación oficial ni sus temporizadores.

## Política de versiones

Cada corrección publicada aumenta el último número: 1.0.3 → 1.0.4 → 1.0.5.
Se agrupan cambios relacionados en una versión, no se aumenta por cada archivo
editado. Una mejora funcional mayor puede pasar a 1.1.0; cambios incompatibles,
a 2.0.0. La versión se define una sola vez en `core/version.py`.

Validar primero la rama de trabajo en pruebas. Promover el mismo commit a
producción únicamente con autorización explícita, sin cambiar la versión
entre validación y promoción. Registrar autor y revisión en Git.
