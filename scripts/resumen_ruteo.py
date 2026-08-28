r"""Resumen del ruteo de modelos: cuántas preguntas fueron a cada modelo,
cuántas hubo que escalar y qué ahorro representa eso.

Se puede correr de dos formas:

    python scripts\resumen_ruteo.py            -> lo muestra en pantalla
    python scripts\resumen_ruteo.py --email    -> además lo manda por mail

Lee claude_engine\logs\ruteo_modelos.csv, que escribe el agente solo.
No modifica nada: es solo lectura.
"""

from __future__ import annotations

import csv
import smtplib
import sys
from collections import Counter
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import settings  # noqa: E402

ARCHIVO = Path(__file__).resolve().parents[1] / "logs" / "ruteo_modelos.csv"

# --- Referencia de costos, solo para estimar el ahorro en el resumen -------
#
# Perfil de tokens de una pregunta típica, medido sobre el bucle real con el
# caché activo (4 vueltas). Si el promedio de vueltas que muestra este mismo
# resumen se aleja mucho de 4, estos números se quedan cortos.
TOKENS_CACHE_LEIDO = 15_990
TOKENS_CACHE_ESCRITO = 7_780
TOKENS_SALIDA = 900

# Precio por millón de tokens (USD). Caché leído = 10% del precio de entrada;
# caché escrito = 125%. Valores de Sonnet 5 desde el 1/9/2026 y Haiku 4.5.
# Si Anthropic cambia la lista de precios, actualizar acá.
PRECIO = {
    "grande": {"lectura": 0.30, "escritura": 3.75, "salida": 15.00},
    "chico": {"lectura": 0.10, "escritura": 1.25, "salida": 5.00},
}
COTIZACION = 6100


def costo_por_pregunta(tarifa: dict) -> float:
    return (
        TOKENS_CACHE_LEIDO * tarifa["lectura"]
        + TOKENS_CACHE_ESCRITO * tarifa["escritura"]
        + TOKENS_SALIDA * tarifa["salida"]
    ) / 1_000_000


def leer(dias: int | None) -> list[dict]:
    if not ARCHIVO.exists():
        return []
    with open(ARCHIVO, encoding="utf-8-sig", newline="") as archivo:
        filas = list(csv.DictReader(archivo, delimiter=";"))
    if dias is None:
        return filas
    corte = datetime.now() - timedelta(days=dias)
    recientes = []
    for fila in filas:
        try:
            momento = datetime.strptime(fila["fecha_hora"], "%Y-%m-%d %H:%M:%S")
        except (ValueError, KeyError):
            continue
        if momento >= corte:
            recientes.append(fila)
    return recientes


def armar_resumen(dias: int | None = None) -> str:
    filas = leer(dias)
    periodo = "desde que se activó" if dias is None else f"de los últimos {dias} días"

    if not filas:
        return (
            f"No hay preguntas registradas {periodo}.\n\n"
            f"Si esperabas ver datos, revisá que el archivo exista:\n{ARCHIVO}\n"
            "Se crea solo la primera vez que alguien hace una pregunta en el chat."
        )

    total = len(filas)
    simples = sum(1 for f in filas if f["clasificacion"] == "simple")
    escaladas = sum(1 for f in filas if f["escalo"] == "si")
    resueltas_chico = sum(
        1 for f in filas if f["escalo"] == "no" and f["clasificacion"] == "simple"
        and f["modo"] == "activo"
    )
    modo = Counter(f["modo"] for f in filas).most_common(1)[0][0]

    ahorro_por_pregunta = (
        costo_por_pregunta(PRECIO["grande"]) - costo_por_pregunta(PRECIO["chico"])
    )
    ahorro_gs = resueltas_chico * ahorro_por_pregunta * COTIZACION

    lineas = [
        f"RESUMEN DE RUTEO DE MODELOS — {periodo}",
        f"Modo: {modo}",
        "",
        f"Preguntas totales:            {total}",
        f"Clasificadas como simples:    {simples}  ({simples / total * 100:.0f}%)",
        f"Clasificadas como complejas:  {total - simples}  ({(total - simples) / total * 100:.0f}%)",
        "",
    ]

    if modo == "sombra":
        lineas += [
            "Estás en modo sombra: todo se respondió con el modelo grande.",
            f"Si activaras el ruteo, {simples} de {total} preguntas habrían ido al modelo chico.",
            "",
        ]
    else:
        tasa = escaladas / simples * 100 if simples else 0
        lineas += [
            f"Resueltas por el modelo chico: {resueltas_chico}",
            f"Escaladas al modelo grande:    {escaladas}  ({tasa:.0f}% de las simples)",
            "",
            f"Ahorro estimado del período:   Gs. {ahorro_gs:,.0f}".replace(",", "."),
            f"  (unos Gs. {ahorro_por_pregunta * COTIZACION:,.0f} por pregunta ruteada)".replace(",", "."),
            "",
        ]
        if simples and tasa > 30:
            lineas += [
                "ATENCIÓN: más de 3 de cada 10 preguntas simples terminaron escalando.",
                "Eso significa que el clasificador está siendo demasiado optimista y",
                "conviene ajustarlo — el ahorro real es menor al esperado.",
                "",
            ]

    if escaladas:
        lineas.append("Motivos de escalada:")
        for motivo, cantidad in Counter(
            f["motivo_escalada"] for f in filas if f["escalo"] == "si"
        ).most_common():
            lineas.append(f"  {cantidad:>3}x  {motivo}")
        lineas.append("")

    lineas.append("Motivos de clasificación como compleja:")
    for motivo, cantidad in Counter(
        f["motivo"] for f in filas if f["clasificacion"] == "compleja"
    ).most_common(8):
        lineas.append(f"  {cantidad:>3}x  {motivo}")
    lineas.append("")

    vueltas = [int(f["vueltas"]) for f in filas if f["vueltas"].isdigit()]
    if vueltas:
        lineas.append(
            f"Vueltas por pregunta: promedio {sum(vueltas) / len(vueltas):.1f}, "
            f"máximo {max(vueltas)} (el tope configurado es {settings.max_tool_rounds})"
        )
        lineas.append("")

    lineas += [
        "Por empresa:",
    ]
    for empresa, cantidad in Counter(f["empresa"] for f in filas).most_common():
        chico = sum(
            1 for f in filas
            if f["empresa"] == empresa and f["escalo"] == "no"
            and f["clasificacion"] == "simple" and f["modo"] == "activo"
        )
        lineas.append(f"  {empresa}: {cantidad} preguntas, {chico} al modelo chico")
    lineas += [
        "",
        f"El detalle pregunta por pregunta está en:\n{ARCHIVO}",
        "Se abre con Excel haciendo doble clic.",
    ]
    return "\n".join(lineas)


def destinatarios() -> list[str]:
    crudo = (settings.alert_email_to or "").replace(";", ",")
    return [parte.strip() for parte in crudo.split(",") if parte.strip()]


def enviar(texto: str) -> None:
    lista = destinatarios()
    if not (settings.smtp_user and settings.smtp_password and lista):
        print("\n[No se envió el mail: falta SMTP_USER, SMTP_PASSWORD o ALERT_EMAIL_TO en el .env]")
        return

    mensaje = EmailMessage()
    mensaje["Subject"] = "Conepasa IA — resumen de ruteo de modelos"
    mensaje["From"] = settings.smtp_user
    mensaje["To"] = lista[0]
    if len(lista) > 1:
        mensaje["Bcc"] = ", ".join(lista[1:])
    mensaje.set_content(texto)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as servidor:
        servidor.starttls()
        servidor.login(settings.smtp_user, settings.smtp_password)
        servidor.send_message(mensaje)
    print(f"\n[Mail enviado a {len(lista)} destinatario(s)]")


if __name__ == "__main__":
    dias = None
    for argumento in sys.argv[1:]:
        if argumento.startswith("--dias="):
            dias = int(argumento.split("=", 1)[1])

    resumen = armar_resumen(dias)
    print(resumen)

    if "--email" in sys.argv:
        enviar(resumen)
