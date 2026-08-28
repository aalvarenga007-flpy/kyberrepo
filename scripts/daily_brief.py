"""Genera y entrega el resumen diario de alertas (email + ventana local).

Pensado para correr una vez por día, disparado por el Programador de tareas
de Windows (ver configurar_alerta_diaria.bat) — no depende de que Streamlit
esté abierto. Reutiliza el mismo DataAnalystAgent y los mismos guardrails de
solo lectura que usa el chat: el resumen se arma con datos reales ejecutados
contra MySQL, no con cifras inventadas por el modelo.
"""

from __future__ import annotations

import smtplib
import ssl
import sys
import webbrowser
from datetime import date, datetime, time, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from pathlib import Path

# Al ejecutarse como script suelto (python scripts/daily_brief.py), Python
# agrega la carpeta "scripts" a sys.path, no la raíz del proyecto. Sin esto,
# "from ai.agent import ..." fallaría con ModuleNotFoundError.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from ai.agent import COMPANY_LABELS, DataAnalystAgent  # noqa: E402
from core import suscripcion  # noqa: E402
from core.audit import write_log  # noqa: E402
from core.config import settings  # noqa: E402
from core.db import get_engine  # noqa: E402

# El resumen se pide en DOS llamadas separadas en vez de una sola.
#
# Motivo: el agente tiene un tope de pasos por conversación (MAX_TOOL_ROUNDS).
# Ekarú, con 5 sucursales y 9 secciones que tocan dominios distintos (ventas,
# compras, mozos, cobranzas, pagos, cheques), se quedaba sin pasos a mitad de
# camino y el informe salía con "No pude completar el análisis". Ejapo pasaba
# porque tiene un solo local.
#
# Partirlo en dos le da a cada mitad su propio presupuesto completo de pasos.
# Cuesta una llamada más de API, pero mucho menos que subir el tope global
# para todas las consultas del sistema. Y si una mitad falla, la otra igual
# sale: antes un tropiezo en cobranzas se llevaba puesto todo el informe.

_PREAMBULO = """Generá una PARTE del resumen ejecutivo diario para Esteban, el dueño del negocio.

REGLA DE ORO — JORNADA OPERATIVA, NO FECHA DE CALENDARIO:
Antes de nada, revisá si existe la vista `v_ventas_jornada` en la base. SI EXISTE, usala como \
fuente para TODO lo que sea ventas del día, por sucursal y acumulado del mes. Esa vista agrupa \
las ventas por JORNADA DE TRABAJO en vez de por fecha de factura: las ventas del viernes son \
todas las que arrancaron ese día, aunque la caja se haya cerrado a las 02:00 del sábado. Es la \
única forma de que el número coincida con el cierre de caja del cajero.
- Columna de fecha: `jornada` (NO uses DATE(Fecha_Hora) ni la tabla `ventas` en crudo).
- Columnas de monto: `venta_contado`, `venta_credito`, `venta_remision`, `venta_total`.
- Cantidad de comprobantes: `comprobantes` (ya cuenta bien las remisiones, que no tienen \
número de factura).
Si la vista NO existe en esa base, trabajá como siempre con la tabla de ventas y el neto \
(`subtotal`), y NO inventes columnas que no viste.

DEUDAS: VISTAS OBLIGATORIAS (regla del dueño, 20/08/2026):
- Para cuentas por COBRAR (cartera, morosidad de clientes, cobranzas) usá SIEMPRE la vista \
`v_deudas_clientes`. NUNCA la tabla `deudas_de_clientes`.
- Para cuentas por PAGAR (deuda con proveedores, vencimientos) usá SIEMPRE la vista \
`v_deudas_proveedores`. NUNCA la tabla `deudas_con_proveedores`.
- Motivo: las tablas crudas repiten la misma deuda muchas veces porque el sincronizador las \
reinserta en cada corrida. Sumarlas da cifras varias veces mayores que la realidad. Las vistas \
se quedan con el registro más reciente de cada deuda y son la única fuente correcta.
- Si alguna de esas vistas no existe en la base, NO uses la tabla cruda como reemplazo: escribí \
una viñeta avisando que esa sección no se puede calcular de forma confiable y seguí con el \
resto del informe.
- En proveedores, el saldo correcto es `saldo_actual`. La vista contiene solo deudas abiertas: \
`total_pagado` en cero significa que no hubo pagos parciales, no que la factura esté impaga \
desde siempre. No lo reportes como anomalía.
- `dias_atraso` tiene valores corruptos en algunas facturas (vencimientos con años imposibles). \
Para rankings de mora, ignorá las filas con `dias_atraso` mayor a 3000. Nunca promedies esa \
columna sin ese filtro.

QUÉ SIGNIFICA CADA TIPO DE VENTA (importante para no confundir al lector):
- CONTADO: se cobró en el momento. Es lo ÚNICO que puede cuadrar contra el cierre de caja.
- CRÉDITO: facturado, se cobra después.
- REMISIÓN: sin factura (vales de premiación, comedor de funcionarios). Es venta real, se \
cobra a fin de mes contra presentación de las remisiones.

FORMATO DE SALIDA (obligatorio, respetalo al pie de la letra):
- El reporte va por SECCIONES. Cada sección arranca con su TÍTULO en una línea sola, con el \
prefijo "### " (tres numerales y un espacio). Ejemplo de línea de título: "### Ventas del día".
- Debajo del título, los datos van como viñetas, cada una en su propia línea, empezando con \
"- " (guion y espacio). UNA sola idea por viñeta.
- No escribas párrafos corridos ni texto suelto fuera de las secciones. Sin saludo ni cierre.
- NO agregues comentarios sobre tu propio proceso ("ahora tengo los datos", "voy a consultar"): \
solo las secciones pedidas.
- Montos en guaraníes como "Gs." con punto de miles. Tono directo y ejecutivo.
- Para ventas Y compras usá siempre el NETO (columna `subtotal`), así ambos son comparables.
- Si una sección aplica pero no tiene datos ese día, resolvela en una viñeta breve.

TENÉS POCOS PASOS DISPONIBLES: no repitas consultas para confirmar lo que ya trajiste, y no \
explores tablas de más. Generá SOLO las secciones que se listan abajo, ninguna otra."""


BRIEF_VENTAS = _PREAMBULO + """

SECCIONES A GENERAR EN ESTA PARTE (solo estas, en este orden):

### Ventas del día
- PRIMERA VIÑETA, SIEMPRE SOLA: la venta TOTAL de la jornada de ayer en Gs. y nada más. No \
metas comparaciones, porcentajes ni aclaraciones en esa línea.
- SEGUNDA VIÑETA: la comparación contra el MISMO DÍA de la semana anterior (no el día \
calendario previo), con el % de variación.
- TERCERA VIÑETA: el desglose de la jornada: contado, crédito y remisiones. Si crédito y \
remisiones son cero, resolvelo diciendo que fue todo contado.

### Ventas del día por sucursal
- INCLUÍ esta sección SOLO si la empresa tiene MÁS DE UNA sucursal. Una viñeta por sucursal con \
su monto TOTAL de la jornada; marcá subas o bajas de 10% o más. Si una sucursal tiene \
remisiones o crédito por un monto relevante, aclarálo entre paréntesis. Si la empresa opera con \
una sola sucursal, NO incluyas esta sección (ni siquiera su título).
- SUCURSAL SIN VENTAS AYER: si una sucursal que venía operando no registra NADA en la jornada \
de ayer, decilo explícitamente como posible problema de datos, no la omitas en silencio.

REGLA DE DÍA INCOMPLETO (obligatoria, definida por el dueño el 09/08/2026):
- El script YA verificó por su cuenta, antes de llamarte, si faltan sucursales. NO gastes una \
consulta en comprobarlo.
- Regla simple: si una sucursal tiene venta en UNA de las dos jornadas y CERO (o no aparece) en \
la otra, NO muestres porcentaje para ella. Escribí "sin datos para comparar" y seguí. Tampoco \
metas esa sucursal en el porcentaje del total.
- Motivo: un porcentaje calculado sobre datos incompletos se lee como una caída del negocio \
cuando en realidad es una falla de sincronización del ERP. Es peor que no dar el número.

### Acumulado del mes
- Venta total del mes en curso (del día 1 hasta ayer) en Gs., y variación vs el mismo tramo \
(los mismos días) del mes anterior.

### Venta por mozo/vendedor
- Ranking de quién vende, con el monto del DÍA de ayer y el ACUMULADO del mes de cada uno. \
En negocios con salón y mozos, es el mozo por sucursal (excluí mostrador, delivery y ventas \
registradas directamente por caja). En comercios sin salón, es el vendedor/caja que registró \
la venta. Mostrá los principales, no la lista completa.

EFICIENCIA: UNA SOLA consulta a `v_ventas_jornada` trayendo las DOS jornadas (la de ayer y la \
del mismo día de la semana anterior) agrupadas por sucursal te resuelve las dos primeras \
secciones completas. El acumulado del mes es otra consulta. Los mozos, una tercera."""


BRIEF_FINANZAS = _PREAMBULO + """

SECCIONES A GENERAR EN ESTA PARTE (solo estas, en este orden). NO generes ninguna sección de \
ventas del día ni por sucursal: eso ya lo cubrió otra parte del informe.

### Compras vs ventas del mes
- Total COMPRADO en el mes (del 1 a ayer) y total VENDIDO en el mes (del 1 a ayer), ambos en \
neto, para ver la relación entre lo que entró y lo que salió en mercadería/compras.
- OBLIGATORIO: además de los dos montos, mostrá SIEMPRE una tercera viñeta con el PORCENTAJE \
que representan las compras sobre las ventas (compras / ventas * 100), con un decimal. No \
omitas nunca esta viñeta: si tenés los dos montos, tenés que calcular el porcentaje.

### Compras del día
- SOLO las compras cuya FECHA DE COMPRA sea AYER (no de días anteriores). Proveedores \
destacados por monto, con cantidad de facturas. Si no hubo compras con fecha de ayer, decilo \
en una sola viñeta (es normal que las facturas se carguen con uno o dos días de atraso).

### Pagos a proveedores
- Órdenes de pago registradas ayer (plata que YA salió): total en Gs. y, si se puede, abierto \
por forma de pago (efectivo / transferencia / cheque).

### Cobranzas
- Cuentas por cobrar en mora relevante (cliente, monto, días de atraso).

### Cuentas por pagar
- Facturas de proveedores próximas a vencer (próximos 7 días) o con mora relevante (proveedor, \
monto, vencimiento).

### Flujo de caja (cheques diferidos)
- INCLUÍ esta sección SOLO si la empresa maneja cheques diferidos (existe la tabla con datos). \
Cheques pendientes que vencen en los próximos 7 días (monto total y proveedor). Si la empresa \
NO tiene cheques diferidos, NO incluyas esta sección (ni siquiera su título)."""


PARTES_DEL_BRIEF = (("ventas", BRIEF_VENTAS), ("finanzas", BRIEF_FINANZAS))

REPORTS_DIR = Path(__file__).resolve().parents[1] / "data" / "alertas"

# --- Control de frescura de datos -------------------------------------------
#
# El informe se arma con lo que hay en MySQL, que a su vez depende de que el
# sincronizador (BI Worker) haya traído los datos desde el ERP del proveedor.
# Si el sync no corrió, o corrió a destiempo, el informe muestra números
# incompletos sin ninguna señal de que lo son. Eso ya pasó: la jornada del
# sábado 01/08/2026 quedó sin PIZZA ROMANA, SPORTBAR ni SAJONIA, y el resumen
# lo presentó como una caída de ventas.
#
# Estas funciones revisan `bi_sync_control` ANTES de generar el resumen y
# devuelven advertencias para mostrar arriba de todo. No arreglan el sync:
# hacen que nunca leas un número incompleto creyendo que está completo.

# Hora de corte de la jornada operativa (la misma que usan las vistas).
HORA_CORTE_JORNADA = 3

# Ventana de servicio nocturno. Un sync que corre acá adentro puede avanzar su
# cursor (`last_id` sobre idventadet) por encima de mesas que están abiertas
# pero todavía no cobradas, y esas filas no se recuperan en la vuelta
# siguiente. Es la causa probable del hueco del 01/08 (sync a las 20:09).
SERVICIO_DESDE = 17
SERVICIO_HASTA = 3


def _estado_sync(database: str) -> dict | None:
    """Lee de `bi_sync_control` cuándo terminó el último sync de Ventas.

    Devuelve None si la tabla no existe o no se puede consultar: en ese caso
    el informe sale igual, solo que sin este control."""
    sql = """
SELECT last_sync_start, last_sync_end, last_sync_status, last_error_message
FROM bi_sync_control
WHERE vista_nombre = 'Ventas'
LIMIT 1
"""
    try:
        engine = get_engine(database)
        with engine.connect() as connection:
            fila = connection.execute(text(sql)).mappings().first()
    except Exception as exc:
        write_log("WARNING", f"No se pudo leer bi_sync_control en {database}: {exc}")
        return None
    return dict(fila) if fila else None


def _alertas_frescura(database: str) -> list[str]:
    """Advertencias sobre la confiabilidad de los datos de ventas de ayer."""
    estado = _estado_sync(database)
    if estado is None:
        return []

    alertas: list[str] = []
    fin = estado.get("last_sync_end")
    inicio = estado.get("last_sync_start")
    status = (estado.get("last_sync_status") or "").lower()

    # La jornada de ayer recién queda cerrada hoy a la hora de corte.
    cierre_jornada = datetime.combine(date.today(), time(HORA_CORTE_JORNADA, 0))

    if fin is None:
        alertas.append(
            "No hay registro de ninguna sincronización de Ventas. Los datos de este "
            "informe pueden estar desactualizados."
        )
    elif fin < cierre_jornada:
        horas = (datetime.now() - fin).total_seconds() / 3600
        alertas.append(
            f"DATOS POSIBLEMENTE INCOMPLETOS: la última sincronización de Ventas terminó "
            f"el {fin.strftime('%d/%m/%Y a las %H:%M')} (hace {horas:.0f} horas), ANTES de "
            f"que cerrara la jornada de ayer. Las ventas de anoche pueden no estar cargadas "
            f"todavía. No tomes las cifras de este informe como definitivas."
        )
    else:
        # El sync alcanzó a correr después del cierre, pero conviene revisar si
        # ARRANCÓ en pleno servicio: ahí es donde puede saltearse mesas.
        arranque = inicio or fin
        hora = arranque.hour
        if hora >= SERVICIO_DESDE or hora < SERVICIO_HASTA:
            alertas.append(
                f"REVISAR: la sincronización arrancó el "
                f"{arranque.strftime('%d/%m/%Y a las %H:%M')}, en pleno servicio nocturno. "
                f"En esos casos el sincronizador puede saltearse mesas abiertas y todavía "
                f"no cobradas, y esas ventas no se recuperan después. Si alguna sucursal "
                f"aparece con cifras muy bajas o en cero, es probable que sea por esto y no "
                f"por una caída real."
            )

    if status == "error":
        detalle = (estado.get("last_error_message") or "").strip()
        detalle = detalle[:200] if detalle else "sin detalle"
        alertas.append(f"La última sincronización de Ventas terminó con ERROR: {detalle}")

    return alertas


def _sucursales_faltantes(database: str) -> list[str]:
    """Compara las sucursales de la jornada de ayer contra las de la semana
    anterior. Si alguna operó hace una semana y ayer no aparece, lo avisa.

    Silencioso si la vista `v_ventas_jornada` no existe en esta base."""
    ayer = date.today() - timedelta(days=1)
    semana_previa = ayer - timedelta(days=7)
    sql = """
SELECT jornada, sucursal
FROM v_ventas_jornada
WHERE jornada IN (:ayer, :previa)
"""
    try:
        engine = get_engine(database)
        with engine.connect() as connection:
            filas = connection.execute(
                text(sql), {"ayer": ayer, "previa": semana_previa}
            ).fetchall()
    except Exception:
        return []

    de_ayer = {f[1] for f in filas if f[0] == ayer}
    de_antes = {f[1] for f in filas if f[0] == semana_previa}
    faltan = sorted(de_antes - de_ayer)
    if not faltan:
        return []
    return [
        f"SUCURSALES SIN DATOS AYER: {', '.join(faltan)} operaron el "
        f"{semana_previa.strftime('%d/%m')} pero no registran ninguna venta el "
        f"{ayer.strftime('%d/%m')}. Antes de leerlo como una caída, verificá la "
        f"sincronización de esa jornada."
    ]


def _anulaciones_recientes(database: str) -> list[str]:
    """Avisa de las ventas que se anularon en las últimas 24 horas.

    El filtro es por `fecha_anulado`, NO por `fecha_venta`. Una venta se puede
    anular varios días después de emitida: el caso que originó esta alerta fue
    una factura del 04/08/2026 por Gs. 15.066.000 anulada el 12/08. Filtrando
    por fecha de venta, esa anulación no habría aparecido en ningún informe.

    Solo cuenta las anulaciones confirmadas (`anulacion_finalizada = 'S'`).
    Las que están en NULL son anulaciones iniciadas pero no terminadas: esas
    ventas siguen siendo válidas.

    Silencioso si la tabla `ventas_anuladas` no existe en esta base."""
    desde = datetime.now() - timedelta(hours=24)
    sql = """
SELECT sucursal, factura, fecha_venta, monto_venta, motivo_anulacion, usuario_anulo
FROM ventas_anuladas
WHERE fecha_anulado >= :desde
  AND UPPER(TRIM(anulacion_finalizada)) = 'S'
ORDER BY monto_venta DESC
"""
    try:
        engine = get_engine(database)
        with engine.connect() as connection:
            filas = connection.execute(text(sql), {"desde": desde}).fetchall()
    except Exception:
        return []

    if not filas:
        return []

    total = sum(float(f[3] or 0) for f in filas)
    detalles: list[str] = []
    for sucursal, factura, fecha_venta, monto, motivo, usuario in filas[:5]:
        emitida = ""
        if fecha_venta is not None and fecha_venta.date() != date.today():
            emitida = f", emitida el {fecha_venta.strftime('%d/%m')}"
        detalles.append(
            f"{sucursal or 'sin sucursal'} · factura {factura or 's/n'} · "
            f"Gs. {float(monto or 0):,.0f}{emitida} · motivo: "
            f"{motivo or 'sin motivo'} · anuló: {usuario or 'sin usuario'}".replace(",", ".")
        )

    resto = ""
    if len(filas) > 5:
        resto = f" (se muestran las 5 mayores de {len(filas)})"

    encabezado = (
        f"VENTAS ANULADAS EN LAS ÚLTIMAS 24 H: {len(filas)} comprobante(s) por "
        f"Gs. {total:,.0f}{resto}. Estas ventas ya NO se cuentan en las cifras de "
        f"este informe. Si alguna fue emitida en días anteriores, los acumulados de "
        f"esos días bajaron respecto de lo informado en su momento."
    ).replace(",", ".")

    return [encabezado] + [f"   · {d}" for d in detalles]


def alertas_de_datos(database: str) -> list[str]:
    """Todas las advertencias de calidad de datos para una empresa."""
    try:
        return (
            _alertas_frescura(database)
            + _sucursales_faltantes(database)
            + _anulaciones_recientes(database)
        )
    except Exception as exc:
        write_log("WARNING", f"No se pudieron calcular las alertas de datos: {exc}")
        return []



def generar_resumen(company: str, database: str) -> str:
    """Arma el resumen de una empresa pidiéndolo en partes.

    Cada parte usa su PROPIO agente, y por lo tanto su propio presupuesto de
    pasos. Si una parte falla, las demás igual salen: se devuelve lo que se
    pudo generar más una nota de qué faltó."""
    bloques: list[str] = []
    for nombre, pregunta in PARTES_DEL_BRIEF:
        try:
            agent = DataAnalystAgent(company=company, database=database)
            texto = (agent.ask([{"role": "user", "content": pregunta}])["text"] or "").strip()
            if texto:
                bloques.append(texto)
            else:
                write_log("WARNING", f"[{company}] La parte '{nombre}' volvió vacía.")
        except Exception as exc:
            write_log("ERROR", f"[{company}] Falló la parte '{nombre}' del resumen: {exc}")
            bloques.append(
                f"### Problema al generar esta sección\n"
                f"- No se pudo completar la parte de {nombre}: {exc}"
            )
    if not bloques:
        raise RuntimeError("Ninguna parte del resumen se pudo generar.")
    return "\n\n".join(bloques)


def _parsear_secciones(texto: str) -> list[dict]:
    """Convierte la respuesta del modelo en una lista de secciones.

    Cada línea que empieza con "#" (marcador "### Título") abre una sección
    nueva; las demás líneas son viñetas que cuelgan de la última sección. Se
    toleran viñetas antes del primer título (van en una sección sin título)."""
    secciones: list[dict] = []
    for linea in texto.splitlines():
        limpia = linea.strip()
        if not limpia:
            continue
        if limpia.startswith("#"):
            titulo = limpia.lstrip("#").strip().rstrip(":").strip()
            secciones.append({"titulo": titulo, "items": []})
        else:
            item = limpia.lstrip("-•*").strip()
            if not item:
                continue
            if not secciones:
                secciones.append({"titulo": None, "items": []})
            secciones[-1]["items"].append(item)
    return secciones


def _secciones_a_html(secciones: list[dict]) -> str:
    if not secciones:
        return "<p><em>Sin novedades relevantes hoy.</em></p>"
    partes = []
    for seccion in secciones:
        if seccion["titulo"]:
            partes.append(f"<h3>{escape(seccion['titulo'])}</h3>")
        if seccion["items"]:
            filas = "".join(f"<li>{escape(item)}</li>" for item in seccion["items"])
            partes.append(f"<ul>{filas}</ul>")
    return "".join(partes)


def _secciones_a_texto_plano(secciones: list[dict]) -> str:
    lineas: list[str] = []
    for seccion in secciones:
        if seccion["titulo"]:
            lineas.append("")
            lineas.append(f"{seccion['titulo']}:")
        for item in seccion["items"]:
            lineas.append(f"  - {item}")
    return "\n".join(lineas).strip()


def _alertas_a_html(alertas: list[str]) -> str:
    if not alertas:
        return ""
    filas = "".join(f"<li>{escape(a)}</li>" for a in alertas)
    return f'<div class="aviso"><strong>&#9888; Calidad de los datos</strong><ul>{filas}</ul></div>'


def _alertas_a_texto_plano(alertas: list[str]) -> str:
    if not alertas:
        return ""
    lineas = ["!! CALIDAD DE LOS DATOS:"]
    lineas += [f"  - {a}" for a in alertas]
    return "\n".join(lineas) + "\n"


def construir_html(
    secciones: dict[str, str], alertas: dict[str, list[str]] | None = None
) -> str:
    alertas = alertas or {}
    bloques = "".join(
        f"<h2>{escape(COMPANY_LABELS.get(company, company))}</h2>"
        f"{_alertas_a_html(alertas.get(company, []))}"
        f"{_secciones_a_html(_parsear_secciones(texto))}"
        for company, texto in secciones.items()
    )
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Conepasa AI · Resumen diario {date.today().isoformat()}</title>
<style>
  body {{ background:#0e0e12; color:#f2f2f2; font-family: 'Segoe UI', Arial, sans-serif;
          padding: 40px; max-width: 720px; margin: 0 auto; }}
  h1 {{ color:#ff5c4d; margin-bottom: 4px; }}
  .fecha {{ color:#9a9aa2; margin-top: 0; }}
  h2 {{ color:#ff5c4d; border-bottom: 1px solid #2a2a30; padding-bottom: 6px;
        margin-top: 36px; }}
  h3 {{ color:#f2f2f2; margin: 20px 0 6px; font-size: 1.02em; }}
  ul {{ padding-left: 22px; margin: 0; }}
  li {{ line-height: 1.6; margin-bottom: 6px; }}
  .aviso {{ background:#3a1f14; border-left: 4px solid #ffa733; color:#ffd9a8;
            padding: 12px 16px; margin: 14px 0 6px; border-radius: 4px; }}
  .aviso strong {{ color:#ffa733; display:block; margin-bottom: 6px; }}
  .aviso ul {{ padding-left: 20px; }}
  .aviso li {{ color:#ffd9a8; }}
</style>
</head>
<body>
<h1>🤖 Conepasa AI · Resumen diario</h1>
<p class="fecha">{date.today().strftime('%d/%m/%Y')}</p>
{bloques}
</body>
</html>"""


def _destinatarios() -> list[str]:
    """Convierte ALERT_EMAIL_TO en una lista de direcciones.

    En el .env las direcciones van separadas por coma (se tolera punto y coma y
    espacios de sobra). El PRIMERO de la lista es el destinatario visible; el
    resto viaja en copia oculta, asi nadie ve las direcciones de los demas ni
    puede usar "responder a todos" sobre un reporte interno.
    """
    crudo = (settings.alert_email_to or "").replace(";", ",")
    vistos, limpias = set(), []
    for parte in crudo.split(","):
        direccion = parte.strip()
        # el .lower() es solo para deduplicar; se envia con el texto original
        if direccion and direccion.lower() not in vistos:
            vistos.add(direccion.lower())
            limpias.append(direccion)
    return limpias


def enviar_email(asunto: str, cuerpo_texto: str, cuerpo_html: str) -> None:
    destinatarios = _destinatarios()
    if not settings.smtp_user or not settings.smtp_password or not destinatarios:
        write_log(
            "WARNING",
            "Resumen diario: falta SMTP_USER, SMTP_PASSWORD o ALERT_EMAIL_TO en .env; "
            "se omite el envío de email.",
        )
        print("AVISO: falta SMTP_USER, SMTP_PASSWORD o ALERT_EMAIL_TO en .env. No se envió el email.")
        return

    mensaje = MIMEMultipart("alternative")
    mensaje["Subject"] = asunto
    mensaje["From"] = settings.smtp_user
    # Solo el primero va en el encabezado To. Los demás NO se declaran en ningún
    # encabezado (por eso no existe una cabecera "Bcc"): van únicamente en la
    # lista de sobre que recibe sendmail, que es lo que los hace copia oculta.
    mensaje["To"] = destinatarios[0]
    mensaje.attach(MIMEText(cuerpo_texto, "plain", "utf-8"))
    mensaje.attach(MIMEText(cuerpo_html, "html", "utf-8"))

    contexto = ssl.create_default_context()
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        server.starttls(context=contexto)
        server.login(settings.smtp_user, settings.smtp_password)
        # sendmail devuelve un diccionario con los destinatarios RECHAZADOS.
        # Si está vacío, el servidor aceptó a todos.
        rechazados = server.sendmail(settings.smtp_user, destinatarios, mensaje.as_string())

    if rechazados:
        write_log("ERROR", f"Resumen diario: destinatarios rechazados por el servidor: {rechazados}")
        print(f"ATENCION: el servidor rechazó estas direcciones: {list(rechazados)}")

    aceptados = [d for d in destinatarios if d not in rechazados]
    write_log("INFO", f"Resumen diario enviado a {len(aceptados)} destinatario(s): {', '.join(aceptados)}")
    print(f"Email enviado a {len(aceptados)} destinatario(s):")
    for direccion in aceptados:
        etiqueta = "visible" if direccion == destinatarios[0] else "copia oculta"
        print(f"  - {direccion}  ({etiqueta})")


def main() -> None:
    # Control comercial ANTES de generar nada.
    #
    # Es el agujero mas facil de dejar abierto: se bloquea la pantalla,
    # el cliente no puede entrar, y a las siete de la manana le sigue
    # llegando igual el resumen de ventas por email. Se consulta la misma
    # vista que usa el login, asi no hay forma de que discrepen.
    #
    # Igual que en la aplicacion: si el control falla por un problema
    # nuestro, el resumen se envia. Nunca al reves.
    estado_suscripcion = suscripcion.estado()
    if suscripcion.bloqueado(estado_suscripcion):
        write_log(
            "WARNING",
            "Resumen diario no generado: la suscripción está en estado "
            f"{estado_suscripcion.get('estado_efectivo')}.",
        )
        print(
            "Resumen diario cancelado: el servicio está suspendido para "
            f"'{estado_suscripcion.get('cliente_id') or 'esta instalación'}'."
        )
        return

    companies = [
        ("ekaru", settings.ekaru_database),
        ("ejapo", settings.ejapo_database),
    ]

    secciones: dict[str, str] = {}
    alertas: dict[str, list[str]] = {}
    for company, database in companies:
        # Las alertas se calculan ANTES del resumen: si el sync no corrió, el
        # aviso tiene que salir igual aunque después falle la generación.
        alertas[company] = alertas_de_datos(database)
        for aviso in alertas[company]:
            write_log("WARNING", f"[{company}] {aviso}")
        try:
            secciones[company] = generar_resumen(company, database)
        except Exception as exc:
            secciones[company] = f"No se pudo generar el resumen de hoy: {exc}"
            write_log("ERROR", f"[{company}] Falló el resumen diario: {exc}")

    texto_plano = "\n\n".join(
        f"=== {COMPANY_LABELS.get(company, company)} ===\n"
        f"{_alertas_a_texto_plano(alertas.get(company, []))}"
        f"{_secciones_a_texto_plano(_parsear_secciones(texto))}"
        for company, texto in secciones.items()
    )
    html = construir_html(secciones, alertas)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    archivo_html = REPORTS_DIR / f"resumen_{date.today().isoformat()}.html"
    archivo_html.write_text(html, encoding="utf-8")

    if settings.alert_show_window:
        webbrowser.open(archivo_html.as_uri())

    if settings.alert_send_email:
        try:
            enviar_email(
                asunto=f"Conepasa AI · Resumen diario {date.today().strftime('%d/%m/%Y')}",
                cuerpo_texto=texto_plano,
                cuerpo_html=html,
            )
        except Exception as exc:
            write_log("ERROR", f"No se pudo enviar el email del resumen diario: {exc}")
            # También por pantalla: si solo va al log, un fallo de envío pasa
            # totalmente desapercibido al correr probar_alerta_diaria.bat.
            print(f"ERROR al enviar el email: {exc}")

    write_log("INFO", f"Resumen diario generado: {archivo_html}")


if __name__ == "__main__":
    main()
