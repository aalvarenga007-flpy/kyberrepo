"""
core/permisos.py
=================
Que puede consultar cada rol. UNICA fuente de verdad.

Las reglas de este archivo NO son suposiciones: salen del relevamiento
real del esquema (information_schema) hecho el 12/08/2026 sobre las dos
bases. Si el ERP agrega columnas nuevas, hay que volver a correr el
relevamiento y actualizar estas listas.

COMO FUNCIONA EL BLOQUEO
------------------------
Tres reglas, y las tres hacen falta. Si sacas una, las otras dos quedan
inutiles:

1. OBJETOS PROHIBIDOS. Tablas y vistas que el rol no puede nombrar.
   Ademas se ocultan de `listar_tablas`, asi que el modelo ni sabe que
   existen.

2. COLUMNAS PROHIBIDAS. Palabras que no pueden aparecer en la consulta.
   Hace falta porque el costo y el margen viven DENTRO de la tabla de
   ventas: no se puede bloquear la tabla entera sin dejar al rol sin
   nada que ver.

3. ASTERISCO PROHIBIDO sobre tablas con columnas bloqueadas.
   Esta es la que cierra el circulo. Sin ella, `SELECT * FROM ventas`
   devuelve Costo_unitario sin que la palabra "costo" aparezca nunca en
   el texto de la consulta, y la regla 2 no se entera.

LIMITE CONOCIDO
---------------
Esto es control por texto de la consulta. Es solido para el uso normal
(los usuarios son duenos y gerentes, no atacantes), pero la barrera
definitiva serian usuarios MySQL separados con GRANT por tabla y por
columna. Queda como endurecimiento para el dia que un cliente lo pida
por auditoria.
"""

from __future__ import annotations

import re


# =====================================================================
# Reglas por rol
# =====================================================================
#
# Las dos empresas tienen esquemas practicamente identicos, asi que las
# reglas son las mismas. Si alguna vez hiciera falta diferenciar, se usa
# OVERRIDES_POR_EMPRESA mas abajo, sin tocar nada de esto.

# Objetos que el rol no puede tocar ni ver listados.
OBJETOS_PROHIBIDOS = {
    "admin": set(),
    "gerencia": set(),
    "operacion": {
        # Compras y costos de insumos
        "compras",
        "compras_y_gastos",
        # Proveedores y pasivos
        "deudas_con_proveedores",
        "ordenes_pago_facturas_aplicadas",
        "ordenes_pago_formas_de_pago",
        # Cartera y cobranzas
        "deudas_de_clientes",
        "cobros_facturas_aplicadas",
        "cobros_formas_de_pago",
        # Finanzas (solo existe en Ejapo, no molesta listarla igual)
        "cheques_diferidos",
        # Vista con costo y margen ya calculados
        "v_ventas",
        "v_ventas_lineas",
    },
    # El rol de presupuestos no usa el chat en absoluto (ver PERMISOS en
    # core/auth.py). Igual se define por si en el futuro se le habilita:
    # solo catalogo y precios de venta, nada de ventas historicas.
    "presupuestos": {
        "compras",
        "compras_y_gastos",
        "deudas_con_proveedores",
        "deudas_de_clientes",
        "ordenes_pago_facturas_aplicadas",
        "ordenes_pago_formas_de_pago",
        "cobros_facturas_aplicadas",
        "cobros_formas_de_pago",
        "cheques_diferidos",
        "movimientos_de_stock",
        "traslado_entre_depositos",
        "ventas",
        "ventas_clean",
        "v_ventas",
        "v_ventas_lineas",
        "v_ventas_diarias",
        "v_ventas_jornada",
        "v_ventas_jornada_lineas",
        "v_ventas_calendario",
        "v_cuadre_caja",
    },
}

# Columnas prohibidas dentro de objetos que SI estan permitidos.
# Cada nombre salio del relevamiento; no hay ninguno inventado.
_COLUMNAS_DE_COSTO = {
    "ultimocosto",            # articulos
    "ultimo_costo_compra",    # stock_de_articulos, v_catalogo_presupuestos
    "ultimo_costo_receta",    # stock_de_articulos
    "costo_unitario",         # ventas, ventas_clean
    "subtotal_costo",         # ventas, ventas_clean, v_ventas_lineas
    "total_costo",            # v_ventas
    "costo_total",            # traslado_entre_depositos
    "costo",                  # compras, traslado_entre_depositos
    "margen",                 # v_ventas
}

_COLUMNAS_DE_PROVEEDOR = {
    "proveedor",              # articulos, productos, ventas
    "idproveedor",
    "id_proveedor",
}

_COLUMNAS_DE_SALDO = {
    "saldo_activo",           # ventas
    "saldo_factura",
    "saldo_actual",
    "saldo_factura_actual",
    "deuda_global",
}

COLUMNAS_PROHIBIDAS = {
    "admin": set(),
    "gerencia": set(),
    "operacion": _COLUMNAS_DE_COSTO | _COLUMNAS_DE_PROVEEDOR | _COLUMNAS_DE_SALDO,
    "presupuestos": _COLUMNAS_DE_COSTO | _COLUMNAS_DE_SALDO,
}

# Objetos permitidos que CONTIENEN alguna columna prohibida. Sobre estos
# no se admite el asterisco: hay que nombrar las columnas una por una.
OBJETOS_CON_COLUMNAS_SENSIBLES = {
    "articulos",
    "productos",
    "stock_de_articulos",
    "ventas",
    "ventas_clean",
    "traslado_entre_depositos",
    "v_ventas",
    "v_ventas_lineas",
    "v_catalogo_presupuestos",
}

# Diferencias por empresa. Vacio a proposito: hoy las reglas son iguales
# en Ekaru y en Ejapo, y esa paridad es deseable. Estructura preparada
# por si algun cliente futuro necesita algo distinto.
OVERRIDES_POR_EMPRESA: dict[str, dict[str, set]] = {}


# =====================================================================
# Consultas a las reglas
# =====================================================================

def _regla(mapa: dict, rol: str, company: str, clave: str) -> set:
    base = set(mapa.get(rol, mapa.get("operacion", set())))
    extra = OVERRIDES_POR_EMPRESA.get(company, {}).get(clave, set())
    return base | set(extra)


def objetos_prohibidos(rol: str, company: str = "") -> set:
    return _regla(OBJETOS_PROHIBIDOS, rol, company, "objetos")


def columnas_prohibidas(rol: str, company: str = "") -> set:
    return _regla(COLUMNAS_PROHIBIDAS, rol, company, "columnas")


def sin_restricciones(rol: str, company: str = "") -> bool:
    return not objetos_prohibidos(rol, company) and not columnas_prohibidas(rol, company)


# =====================================================================
# Filtros para las herramientas de exploracion
# =====================================================================

def filtrar_objetos(rol: str, company: str, objetos) -> list:
    """Saca de la lista de tablas las que el rol no puede ver."""
    prohibidos = {nombre.lower() for nombre in objetos_prohibidos(rol, company)}
    if not prohibidos:
        return list(objetos)
    return [nombre for nombre in objetos if str(nombre).lower() not in prohibidos]


def objeto_permitido(rol: str, company: str, objeto: str) -> bool:
    prohibidos = {nombre.lower() for nombre in objetos_prohibidos(rol, company)}
    return str(objeto).strip().lower() not in prohibidos


def filtrar_columnas(rol: str, company: str, columnas: list[dict]) -> list[dict]:
    """
    Saca las columnas prohibidas del resultado de `ver_columnas`.

    Que el modelo ni siquiera las vea evita que intente usarlas y se
    choque contra el guard, que gastaria una vuelta del bucle al pedo.
    """
    prohibidas = columnas_prohibidas(rol, company)
    if not prohibidas:
        return list(columnas)
    return [
        columna
        for columna in columnas
        if str(columna.get("nombre", "")).strip().lower() not in prohibidas
    ]


# =====================================================================
# Validacion de la consulta SQL
# =====================================================================

# Un asterisco usado como selector de columnas. Cubre "SELECT *",
# "SELECT DISTINCT *", "SELECT a.*" y ", *". NO marca COUNT(*) ni las
# multiplicaciones, que son legitimas.
_PATRONES_ASTERISCO = (
    re.compile(r"\bSELECT\s+(?:DISTINCT\s+)?\*", re.IGNORECASE),
    re.compile(r"[,\s]\s*[A-Za-z_][A-Za-z0-9_]*\.\*"),
    re.compile(r",\s*\*"),
)


def _palabra_presente(texto_mayus: str, palabra: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(palabra.upper())}(?!\w)", texto_mayus) is not None


def validar_consulta(rol: str, company: str, consulta: str) -> str:
    """
    Devuelve un mensaje de rechazo, o cadena vacia si la consulta esta
    permitida para ese rol.

    El mensaje esta escrito para que lo lea el modelo: le dice que hizo
    mal y como reformular, asi corrige solo en la vuelta siguiente en
    lugar de insistir con lo mismo.
    """
    if sin_restricciones(rol, company):
        return ""

    texto = str(consulta)
    texto_mayus = f" {texto.upper()} "

    # Regla 1: objetos prohibidos
    for objeto in sorted(objetos_prohibidos(rol, company)):
        if _palabra_presente(texto_mayus, objeto):
            return (
                f"Tu perfil de usuario no tiene acceso a '{objeto}'. "
                "No insistas con esa tabla ni busques otra forma de llegar a "
                "esos datos: no están disponibles para este usuario. Respondé "
                "lo que sí se pueda con las tablas permitidas, o explicá que "
                "esa información requiere un perfil con más permisos."
            )

    # Regla 2: columnas prohibidas
    for columna in sorted(columnas_prohibidas(rol, company)):
        if _palabra_presente(texto_mayus, columna):
            return (
                f"Tu perfil de usuario no tiene acceso a la columna '{columna}'. "
                "Los datos de costo, margen, proveedor y saldos no están "
                "disponibles para este usuario. Reformulá la consulta usando "
                "solo columnas de venta e informá que esa parte no la podés "
                "responder con este perfil."
            )

    # Regla 3: asterisco sobre objetos con columnas sensibles
    usa_asterisco = any(patron.search(texto) for patron in _PATRONES_ASTERISCO)
    if usa_asterisco:
        for objeto in sorted(OBJETOS_CON_COLUMNAS_SENSIBLES):
            if _palabra_presente(texto_mayus, objeto):
                return (
                    f"No podés usar SELECT * sobre '{objeto}' con este perfil, "
                    "porque esa tabla contiene columnas restringidas. Escribí "
                    "la consulta nombrando explícitamente las columnas que "
                    "necesitás. Si no sabés cuáles hay, usá 'ver_columnas' "
                    "primero: te va a mostrar solamente las que podés usar."
                )

    return ""


# =====================================================================
# Texto para el system prompt
# =====================================================================

def texto_para_prompt(rol: str, company: str = "") -> str:
    """
    Parrafo que se agrega al system prompt para que el modelo sepa de
    antemano que no puede pedir.

    No reemplaza al guard: es para que la respuesta al usuario sea
    prolija ("no tengo acceso a eso") en vez de que el modelo choque
    contra el bloqueo y gaste vueltas del bucle intentando rodearlo.
    """
    if sin_restricciones(rol, company):
        return ""

    objetos = sorted(objetos_prohibidos(rol, company))
    columnas = sorted(columnas_prohibidas(rol, company))

    partes = [
        "",
        "LIMITES DE ESTE USUARIO (obligatorio, no negociable):",
        "El usuario que está haciendo la pregunta tiene un perfil con acceso "
        "restringido. No podés mostrarle, calcularle ni deducirle información "
        "de costos, márgenes, rentabilidad, proveedores, compras, deudas ni "
        "saldos, ni siquiera de forma indirecta o aproximada.",
    ]

    if objetos:
        partes.append(
            "- Tablas y vistas fuera de tu alcance: " + ", ".join(objetos) + "."
        )
    if columnas:
        partes.append(
            "- Columnas fuera de tu alcance en cualquier tabla: "
            + ", ".join(columnas)
            + "."
        )

    partes.extend(
        [
            "- No uses SELECT * sobre tablas que contengan esas columnas. "
            "Nombrá las columnas explícitamente.",
            "- Cuando expliques que no podés responder algo, hablá en términos "
            "de negocio ('datos de compras', 'información de costos') y NUNCA "
            "menciones nombres internos de tablas ni de columnas. El usuario no "
            "tiene por qué conocer la estructura de la base.",
            "- Si la pregunta requiere algo de eso, no intentes rodearlo con "
            "otra consulta. Respondé con naturalidad que ese dato no está "
            "disponible para su perfil y sugerí que lo consulte con alguien "
            "de gerencia. Después contestá la parte de la pregunta que sí "
            "puedas responder.",
            "",
        ]
    )
    return "\n".join(partes)
