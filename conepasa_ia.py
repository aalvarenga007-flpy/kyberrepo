# -*- coding: utf-8 -*-
"""
Conepasa IA - Cheques diferidos: conexion con el modelo.

Este archivo es la "cara" de la plataforma: recibe una pregunta en lenguaje
natural, deja que el modelo elija que herramienta usar, ejecuta el CALCULO en
conepasa_cheques.py (numeros exactos) y devuelve la respuesta escrita.

Uso:
  1) pip install anthropic pandas openpyxl
  2) Poner la API key en la variable de entorno ANTHROPIC_API_KEY
  3) python conepasa_ia.py            -> modo pregunta/respuesta
     python conepasa_ia.py --test     -> prueba OFFLINE (sin API, solo datos)

El modo --test no necesita API key: sirve para confirmar que la ruta del Excel
y los numeros estan bien antes de conectar el modelo.
"""

import os
import sys
import json
from datetime import date

import conepasa_cheques as cc

# --- Modelo ----------------------------------------------------------------
# Para elegir la herramienta correcta alcanza y sobra un modelo liviano.
# Haiku es el mas economico para llamadas frecuentes. Podes subir a
# "claude-sonnet-4-6" o "claude-opus-4-8" si queres respuestas mas elaboradas.
# Lista oficial y vigente: https://docs.claude.com/en/docs/about-claude/models/overview
MODELO = "claude-haiku-4-5-20251001"

SISTEMA = (
    "Sos el asistente de Conepasa para cheques diferidos. La fecha de hoy es {hoy}. "
    "Cuando te pregunten por cheques (que entro, que vence, pendientes, del mes, de ayer), "
    "USA SIEMPRE las herramientas disponibles para obtener los numeros; nunca los inventes "
    "ni los estimes. Respunde en espanol, en guaranies, de forma breve y clara. "
    "Si la herramienta trae un detalle por proveedor, resumilo; no hace falta listar todo "
    "salvo que lo pidan."
)


def preguntar(pregunta: str, hoy: date = None) -> str:
    """Responde una pregunta usando el modelo + las funciones de calculo."""
    import anthropic  # se importa aca para que --test funcione sin el SDK

    hoy = hoy or date.today()
    df = cc.cargar_cheques()
    client = anthropic.Anthropic()  # toma ANTHROPIC_API_KEY del entorno

    messages = [{"role": "user", "content": pregunta}]

    while True:
        resp = client.messages.create(
            model=MODELO,
            max_tokens=1024,
            system=SISTEMA.format(hoy=hoy.isoformat()),
            tools=cc.HERRAMIENTAS,
            messages=messages,
        )

        if resp.stop_reason != "tool_use":
            # respuesta final en texto
            return "".join(b.text for b in resp.content if b.type == "text").strip()

        # el modelo pidio una o mas herramientas: las ejecutamos
        messages.append({"role": "assistant", "content": resp.content})
        resultados = []
        for b in resp.content:
            if b.type == "tool_use":
                fn = cc.FUNCIONES.get(b.name)
                salida = fn(df, hoy) if fn else {"error": "herramienta desconocida"}
                resultados.append({
                    "type": "tool_result",
                    "tool_use_id": b.id,
                    "content": json.dumps(salida, ensure_ascii=False),
                })
        messages.append({"role": "user", "content": resultados})


def _test_offline(hoy: date = None):
    """Valida ruta + datos sin llamar a la API."""
    hoy = hoy or date.today()
    df = cc.cargar_cheques()
    print(f"OK - {len(df)} cheques operativos leidos.")
    print(f"Fecha de corte: {hoy.isoformat()}\n")
    etiquetas = {
        "entraron_ayer": "Entraron ayer",
        "entran_hoy": "Entran hoy (a depositar)",
        "entraron_en_mes": "Ya entraron este mes",
        "pendientes_del_mes": "Pendientes de deposito (mes)",
    }
    for nombre, fn in cc.FUNCIONES.items():
        r = fn(df, hoy)
        print(f"  {etiquetas[nombre]:32s}: {r['cantidad']:3d} cheques  {r['monto_total_fmt']}")


def _repl():
    """Bucle simple pregunta/respuesta por consola."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Falta la variable ANTHROPIC_API_KEY. Ponela antes de usar el modo pregunta.")
        print("Mientras tanto podes correr:  python conepasa_ia.py --test")
        return
    print("Conepasa IA - Cheques. Escribi tu pregunta (o 'salir').")
    while True:
        try:
            q = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if q.lower() in {"salir", "exit", "quit", ""}:
            break
        try:
            print(preguntar(q))
        except Exception as e:
            print("Error:", e)


if __name__ == "__main__":
    if "--test" in sys.argv:
        _test_offline()
    else:
        _repl()
