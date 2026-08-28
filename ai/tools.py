"""Definición de las herramientas que Claude puede usar para investigar la
base de datos antes de responder. En vez de recibir todo el esquema volcado
en el prompt (34 tablas de una sola vez), Claude elige qué mirar, igual que
lo haría un analista humano explorando una base de datos por primera vez.
"""

TOOLS = [
    {
        "name": "listar_tablas",
        "description": (
            "Devuelve la lista de tablas disponibles en la base de datos de "
            "la empresa que se está consultando. Usar primero si no sabés "
            "qué tablas existen o cómo se llaman."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "ver_columnas",
        "description": (
            "Devuelve el nombre y tipo de dato de cada columna de una tabla "
            "específica. Usar antes de escribir una consulta sobre una tabla "
            "que todavía no inspeccionaste en esta conversación."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tabla": {
                    "type": "string",
                    "description": "Nombre exacto de la tabla, tal como aparece en listar_tablas.",
                }
            },
            "required": ["tabla"],
        },
    },
    {
        "name": "ejecutar_sql",
        "description": (
            "Ejecuta una consulta SQL de SOLO LECTURA (SELECT o WITH...SELECT) "
            "contra la base de datos de la empresa activa y devuelve las filas "
            "resultantes. Esta es la ÚNICA forma válida de obtener una cifra "
            "real: nunca calcules ni estimes un número sin haberlo obtenido acá. "
            "Si la consulta falla, vas a recibir el error real de MySQL para "
            "corregirla."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "consulta": {
                    "type": "string",
                    "description": "Consulta SQL completa, lista para ejecutar.",
                }
            },
            "required": ["consulta"],
        },
    },
    {
        "name": "proyectar_tendencia",
        "description": (
            "Calcula una proyección estadística (regresión lineal simple) a partir de una "
            "serie de valores históricos REALES que ya obtuviste con ejecutar_sql (por "
            "ejemplo, ventas totales de cada uno de los últimos 6 a 12 meses, en orden "
            "cronológico, el más antiguo primero). Es la ÚNICA forma válida de proyectar un "
            "valor futuro: nunca inventes ni calcules una proyección de memoria o 'a ojo'. "
            "Devuelve el valor proyectado para cada período futuro pedido, más la variación "
            "histórica típica entre períodos, para que puedas comunicar qué tan confiable es "
            "la estimación."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "valores": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": (
                        "Valores históricos reales, en orden cronológico (el más antiguo "
                        "primero). Mínimo 3 valores; con menos de 6 la proyección es poco "
                        "confiable y hay que decirlo así."
                    ),
                },
                "periodos_a_proyectar": {
                    "type": "integer",
                    "description": "Cantidad de períodos futuros a proyectar (por ejemplo 1 para el próximo mes, 3 para el próximo trimestre).",
                },
            },
            "required": ["valores", "periodos_a_proyectar"],
        },
    },
    {
        "name": "calcular",
        "description": (
            "Evalúa una expresión aritmética exacta (suma, resta, multiplicación, división, "
            "potencia) sobre números que ya obtuviste con ejecutar_sql o con otras "
            "herramientas. Usala SIEMPRE que necesites combinar, comparar o aplicar una tasa "
            "a números reales — por ejemplo: aplicar una variación porcentual a un total, "
            "sacar un promedio o una diferencia entre dos consultas, convertir una tasa a "
            "guaraníes. NUNCA hagas esa cuenta 'de cabeza' en tu respuesta: el resultado "
            "final tiene que salir de esta herramienta para ser exacto. "
            "Ejemplo de expresión válida: '1715670826 * (1 - 0.034)'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "expresion": {
                    "type": "string",
                    "description": (
                        "Expresión aritmética con números literales, por ejemplo "
                        "'1234 * 1.05' o '(500-450)/450*100'. No uses nombres de variables "
                        "ni funciones, solo números y operadores +, -, *, /, **, ()."
                    ),
                }
            },
            "required": ["expresion"],
        },
    },
]
