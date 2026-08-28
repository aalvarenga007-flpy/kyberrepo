"""Verificación de que las cifras del texto salieron de los datos.

El principio de diseño del agente es que todo número final salga de una consulta
SQL ejecutada de verdad. Pero entre el resultado de la consulta y la prosa que el
modelo escribe no había ningún control: nada garantizaba que el "Gs. X" del texto
fuera realmente un valor que devolvió una consulta (o un cálculo determinista de
la herramienta `calcular`).

Este módulo cierra ese hueco. Extrae los montos en guaraníes de la respuesta y
verifica que cada uno esté respaldado por algún valor de los resultados reales.
Es una red de seguridad, no un bloqueo: reporta los montos sin respaldo para
loguearlos y mostrarlos, sin frenar la respuesta.

Se enfoca en montos con prefijo "Gs." (el formato que el sistema obliga a usar
para dinero) y sobre un umbral, que es donde viven los errores graves como el
resumen inflado del 04/08/2026. Ignora porcentajes y conteos chicos a propósito,
para no generar falsas alarmas por redondeos.
"""

from __future__ import annotations

import re

import pandas as pd

# "Gs. 1.403.940.380" | "Gs 45.924.727,50" | "GS. 21.524.625" | "Gs. 3400"
# Dos formas: con separador de miles (1-3 dígitos y grupos de .DDD) o dígitos
# corridos. Así no se traga el punto final de una oración ("... Gs. 66.072.748.").
_MONTO_GS = re.compile(
    r"[Gg][Ss]\.?\s*(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?)"
)

# Palabras que, si vienen justo antes del monto, indican que es un LÍMITE o umbral
# retórico ("montos bajo Gs. 100.000", "más de Gs. 30 millones"), no una cifra que
# el modelo afirme haber obtenido de una consulta. Esos no se verifican.
_LIMITE_ANTES = re.compile(
    r"(bajo|debajo de|por debajo de|menos de|menor(?:es)? a|inferior(?:es)? a|"
    r"m[aá]s de|mayor(?:es)? a|superior(?:es)? a|arriba de|por encima de|"
    r"sobre|hasta|no super\w+)\s*$",
    re.IGNORECASE,
)


def _texto_a_numero(texto: str) -> float | None:
    """Convierte un número en formato paraguayo a float.
    '1.403.940.380' -> 1403940380.0 ; '45.924.727,50' -> 45924727.5
    """
    limpio = texto.strip().rstrip(".")
    limpio = limpio.replace(".", "").replace(",", ".")
    try:
        return float(limpio)
    except ValueError:
        return None


def montos_en_texto(texto: str) -> list[tuple[str, float]]:
    """Devuelve [(fragmento_original, valor), ...] de cada monto 'Gs. ...' del texto."""
    montos = []
    for match in _MONTO_GS.finditer(texto or ""):
        valor = _texto_a_numero(match.group(1))
        if valor is not None:
            montos.append((match.group(0), valor))
    return montos


def numeros_confiables(dataframes: list, calculos: list) -> set[float]:
    """Reúne todos los valores numéricos en los que SÍ se puede confiar: las celdas
    de los resultados SQL y los resultados de la herramienta `calcular` /
    `proyectar_tendencia`."""
    confiables: set[float] = set()

    for dataframe in dataframes:
        if dataframe is None or not hasattr(dataframe, "columns"):
            continue
        for columna in dataframe.columns:
            serie = pd.to_numeric(dataframe[columna], errors="coerce").dropna()
            for valor in serie.tolist():
                confiables.add(float(valor))

    for calculo in calculos:
        try:
            confiables.add(float(calculo))
        except (TypeError, ValueError):
            continue

    return confiables


def _esta_respaldado(valor: float, confiables: set[float], tolerancia: float) -> bool:
    """True si `valor` coincide con algún número confiable dentro de la tolerancia
    relativa. La tolerancia absorbe redondeos ('Gs. 45,9 millones' vs 45.924.727)
    sin dejar pasar errores de escala/duplicación (que son de 20x a 50x)."""
    for confiable in confiables:
        base = max(abs(confiable), 1.0)
        if abs(valor - confiable) / base <= tolerancia:
            return True
    return False


def verificar_cifras(
    texto: str,
    confiables: set[float],
    umbral: float = 100_000,
    tolerancia: float = 0.02,
) -> list[dict]:
    """Devuelve la lista de montos del texto que NO están respaldados por ningún
    valor real. Cada item: {'texto': 'Gs. ...', 'valor': float}. Lista vacía = todo
    verificado. Solo mira montos por encima de `umbral` (los chicos no son el riesgo)."""
    sin_respaldo = []
    for match in _MONTO_GS.finditer(texto or ""):
        valor = _texto_a_numero(match.group(1))
        if valor is None or abs(valor) < umbral:
            continue
        # Si el monto viene detrás de una palabra de límite ("bajo", "más de"...),
        # es un umbral retórico, no una cifra de dato: no se verifica.
        contexto_previo = texto[max(0, match.start() - 25):match.start()]
        if _LIMITE_ANTES.search(contexto_previo):
            continue
        if not _esta_respaldado(valor, confiables, tolerancia):
            sin_respaldo.append({"texto": match.group(0), "valor": valor})
    return sin_respaldo
