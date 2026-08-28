"""Tests de core/verificacion.py — la red de seguridad de cifras.

Puro, sin base de datos ni API. Corre con pytest o directo:
    python tests/test_verificacion.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.verificacion import (  # noqa: E402
    montos_en_texto,
    numeros_confiables,
    verificar_cifras,
)


def test_parsea_montos_formato_paraguayo():
    montos = dict(montos_en_texto("Ventas Gs. 45.924.727 y antes Gs. 66.072.748."))
    assert montos["Gs. 45.924.727"] == 45924727.0
    assert montos["Gs. 66.072.748"] == 66072748.0


def test_parsea_monto_con_decimales():
    (_, valor), = montos_en_texto("Total Gs. 1.234.567,50 exacto.")
    assert valor == 1234567.50


def test_numeros_confiables_desde_dataframe_y_calculos():
    df = pd.DataFrame({"total": [45924727, 331500], "cant": [55, 3]})
    confiables = numeros_confiables([df], [-30.5])
    assert 45924727.0 in confiables
    assert 331500.0 in confiables
    assert -30.5 in confiables


def test_detecta_el_bug_del_04_08():
    """El caso real: el texto dice Gs. 1.403.940.380 pero la consulta devolvió 45.924.727."""
    texto = "Las ventas de ayer fueron Gs. 1.403.940.380 en Casa Matriz."
    confiables = numeros_confiables([pd.DataFrame({"total": [45924727]})], [])
    sin_respaldo = verificar_cifras(texto, confiables)
    assert len(sin_respaldo) == 1
    assert sin_respaldo[0]["texto"] == "Gs. 1.403.940.380"


def test_no_marca_cifra_correcta():
    texto = "Las ventas de ayer fueron Gs. 45.924.727 en Casa Matriz."
    confiables = numeros_confiables([pd.DataFrame({"total": [45924727]})], [])
    assert verificar_cifras(texto, confiables) == []


def test_tolera_redondeo_razonable():
    """'Gs. 45.900.000' (redondeo de 45.924.727) no debe marcarse."""
    texto = "Cerca de Gs. 45.900.000 en ventas."
    confiables = numeros_confiables([pd.DataFrame({"total": [45924727]})], [])
    assert verificar_cifras(texto, confiables) == []


def test_ignora_montos_chicos():
    """Montos por debajo del umbral no se verifican (no son el riesgo)."""
    texto = "El comprobante más chico fue Gs. 3.400."
    assert verificar_cifras(texto, set()) == []


def test_verifica_valor_derivado_de_calcular():
    """Una diferencia que el modelo obtuvo con `calcular` sí cuenta como respaldada."""
    texto = "La caída fue de Gs. 20.148.021 entre las dos semanas."
    # 66.072.748 - 45.924.727 = 20.148.021, calculado con la herramienta.
    confiables = numeros_confiables(
        [pd.DataFrame({"a": [66072748, 45924727]})], [20148021.0]
    )
    assert verificar_cifras(texto, confiables) == []


def test_ignora_umbral_retorico():
    """Caso real del brief en vivo: 'montos bajos, bajo Gs. 100.000' es un límite,
    no una cifra de dato, así que no debe marcarse aunque no esté en los resultados."""
    texto = "Compras del 04/08 con montos bajos, bajo Gs. 100.000, nada relevante."
    assert verificar_cifras(texto, set()) == []
    # pero un monto grande afirmado como dato (sin palabra de límite) sí se marca
    texto2 = "Las ventas fueron Gs. 999.999.999 ayer."
    assert len(verificar_cifras(texto2, set())) == 1


def test_varias_cifras_mezcla_ok_y_mala():
    texto = "Ventas Gs. 45.924.727 (bien) pero compras Gs. 999.888.777 (inventado)."
    confiables = numeros_confiables([pd.DataFrame({"v": [45924727, 8200788]})], [])
    sin_respaldo = verificar_cifras(texto, confiables)
    assert [x["texto"] for x in sin_respaldo] == ["Gs. 999.888.777"]


def _run_all():
    fallos = 0
    for nombre, funcion in sorted(globals().items()):
        if nombre.startswith("test_") and callable(funcion):
            try:
                funcion()
                print(f"  OK   {nombre}")
            except AssertionError as exc:
                fallos += 1
                print(f"  FALLA {nombre}: {exc}")
    print(f"\n{'TODOS OK' if not fallos else f'{fallos} FALLA(S)'}")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
