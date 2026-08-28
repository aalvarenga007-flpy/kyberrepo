"""
core/consumo.py
================
Medicion de consumo y control de cupo. UNICA fuente de verdad sobre
cuantas consultas le quedan a un cliente.

QUE MIDE Y QUE MUESTRA
----------------------
Son dos cosas distintas a proposito:

- Al CLIENTE se le muestran CONSULTAS. Es una unidad que entiende y que
  puede predecir. "Me quedan 188 de 500."

- Por DENTRO se miden TOKENS y COSTO EN DOLARES. Es lo que realmente
  cuesta. Dos clientes con el mismo plan pueden costar muy distinto, y
  ese numero es el que sirve para calibrar precios.

Si le mostraramos creditos ponderados por token, el usuario no entiende
por que una pregunta le descuenta 1 y otra 7, y esa discusion se la come
soporte en cada llamada.

QUE NO DESCUENTA CUPO
---------------------
El brief diario (es automatico, el cliente no lo pidio esa manana), los
reintentos y las consultas que fallan. Todo eso se guarda igual con
computa=0: se mide para costo interno, pero cobrarle al cliente un error
propio es la peor primera impresion posible.

MULTIEMPRESA
------------
La empresa NO sale de una variable de entorno: sale de la sesion activa.
Un usuario con acceso a varias empresas consume de la bolsa de aquella
en la que esta parado en ese momento. Para un cliente con una sola
empresa esto funciona igual, sin configuracion extra: el alta completa
de un cliente nuevo es una fila en `suscripciones`.

SI FALTA CONFIGURACION
----------------------
Si una empresa no tiene fila en `suscripciones`, el modulo DEJA PASAR la
consulta y marca configurado=False. Bloquear a un cliente por una fila
que falta seria peor que el problema. Pero no lo esconde: el badge dice
"sin plan configurado" y el panel de admin lo muestra en rojo.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import text

from core.db import get_engine

try:
    from core.config import settings
except Exception:  # pragma: no cover
    settings = None


log = logging.getLogger(__name__)


# =====================================================================
# Configuracion
# =====================================================================

def _base_auth() -> str:
    """
    Nombre de la base de autenticacion. Se busca en settings por si
    alguna instalacion la renombra; si no esta, el valor por defecto.
    """
    for atributo in ("auth_database", "mysql_auth_db", "auth_db"):
        valor = getattr(settings, atributo, None) if settings else None
        if valor:
            return str(valor)
    return "conepasa_auth"


# Umbral a partir del cual un plan se considera sin limite real. El plan
# 'interno' usa 999999; cualquier cosa de ese orden no se muestra como
# fraccion porque "312 / 999999" no le dice nada a nadie.
LIMITE_ILIMITADO = 100_000

# Avisos al usuario. El primero es informativo, el segundo es el que
# dispara la llamada comercial.
UMBRAL_AVISO = 0.80
UMBRAL_CRITICO = 0.90


# ---------------------------------------------------------------------
# Precios por millon de tokens (USD).
#
# OJO: son estimaciones para costo interno, no facturacion. Sirven para
# comparar clientes entre si y detectar al que se sale de la curva.
# Revisalos contra la factura real cada tanto.
# ---------------------------------------------------------------------
PRECIOS_USD_POR_MTOK = {
    "sonnet": {"in": 3.00, "out": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "haiku":  {"in": 1.00, "out": 5.00,  "cache_read": 0.10, "cache_write": 1.25},
}
_PRECIO_POR_DEFECTO = PRECIOS_USD_POR_MTOK["sonnet"]


def _tarifa(modelo: str | None) -> dict:
    nombre = (modelo or "").lower()
    if "haiku" in nombre:
        return PRECIOS_USD_POR_MTOK["haiku"]
    if "sonnet" in nombre:
        return PRECIOS_USD_POR_MTOK["sonnet"]
    # Modelo desconocido: se cobra al precio mas caro conocido, para no
    # subestimar el costo por accidente.
    return _PRECIO_POR_DEFECTO


def costo_estimado(
    modelo: str | None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    tokens_cache_read: int = 0,
    tokens_cache_write: int = 0,
) -> float:
    t = _tarifa(modelo)
    total = (
        tokens_in * t["in"]
        + tokens_out * t["out"]
        + tokens_cache_read * t["cache_read"]
        + tokens_cache_write * t["cache_write"]
    )
    return round(total / 1_000_000, 6)


# =====================================================================
# Ciclo de facturacion
# =====================================================================

def normalizar_dia_ciclo(dia: int | None) -> int:
    """
    El dia de ciclo se limita a 1-28.

    Si se permitiera 30 o 31, febrero no tiene esos dias y el ciclo se
    parte: un cliente con dia_ciclo=31 no renovaria en febrero, o
    renovaria dos veces segun como se resuelva. Con tope en 28 funciona
    los doce meses sin excepciones.
    """
    try:
        valor = int(dia)
    except (TypeError, ValueError):
        return 1
    return max(1, min(28, valor))


def inicio_de_ciclo(hoy: date, dia_ciclo: int) -> date:
    """Fecha en que arranco el ciclo vigente para esa fecha."""
    dia = normalizar_dia_ciclo(dia_ciclo)
    if hoy.day >= dia:
        return date(hoy.year, hoy.month, dia)
    if hoy.month == 1:
        return date(hoy.year - 1, 12, dia)
    return date(hoy.year, hoy.month - 1, dia)


def fin_de_ciclo(inicio: date) -> date:
    """Fecha en que renueva el cupo (primer dia del ciclo siguiente)."""
    if inicio.month == 12:
        return date(inicio.year + 1, 1, inicio.day)
    return date(inicio.year, inicio.month + 1, inicio.day)


# =====================================================================
# Estado del cupo
# =====================================================================

@dataclass
class EstadoCupo:
    empresa: str
    usuario: str
    ciclo_inicio: date
    ciclo_fin: date

    plan_codigo: str = ""
    plan_nombre: str = ""
    configurado: bool = True

    consultas_usadas: int = 0
    consultas_limite: int = 0
    tolerancia_pct: int = 0

    tope_usuario: int | None = None
    usuario_usadas: int = 0

    presupuestos_usados: int = 0
    presupuestos_limite: int | None = None

    # ---------------- Empresa ----------------

    @property
    def ilimitado(self) -> bool:
        return self.consultas_limite >= LIMITE_ILIMITADO

    @property
    def limite_con_tolerancia(self) -> int:
        return int(self.consultas_limite * (1 + self.tolerancia_pct / 100))

    @property
    def restantes(self) -> int:
        return max(0, self.consultas_limite - self.consultas_usadas)

    @property
    def porcentaje(self) -> float:
        if self.consultas_limite <= 0:
            return 0.0
        return self.consultas_usadas / self.consultas_limite

    # ---------------- Usuario ----------------

    @property
    def tiene_tope_propio(self) -> bool:
        return bool(self.tope_usuario)

    @property
    def restantes_usuario(self) -> int:
        if not self.tiene_tope_propio:
            return self.restantes
        return max(0, int(self.tope_usuario) - self.usuario_usadas)

    # ---------------- Semaforo ----------------

    @property
    def bloqueado(self) -> bool:
        if self.ilimitado or not self.configurado:
            return False
        if self.tiene_tope_propio and self.usuario_usadas >= int(self.tope_usuario):
            return True
        return self.consultas_usadas >= self.limite_con_tolerancia

    @property
    def nivel(self) -> str:
        """ok | aviso | critico | bloqueado | sin_plan"""
        if not self.configurado:
            return "sin_plan"
        if self.ilimitado:
            return "ok"
        if self.bloqueado:
            return "bloqueado"
        if self.porcentaje >= UMBRAL_CRITICO:
            return "critico"
        if self.porcentaje >= UMBRAL_AVISO:
            return "aviso"
        return "ok"

    @property
    def motivo_bloqueo(self) -> str:
        """Texto para el usuario final. Sin jerga tecnica."""
        if not self.bloqueado:
            return ""
        if self.tiene_tope_propio and self.usuario_usadas >= int(self.tope_usuario):
            return (
                f"Alcanzaste tu límite personal de {self.tope_usuario} consultas "
                f"para este período. El cupo renueva el "
                f"{self.ciclo_fin.strftime('%d/%m')}. Si necesitás más, pedile "
                "a un administrador que te amplíe el tope."
            )
        return (
            f"La empresa alcanzó el límite de {self.consultas_limite} consultas "
            f"del plan contratado. El cupo renueva el "
            f"{self.ciclo_fin.strftime('%d/%m')}. Para seguir consultando antes "
            "de esa fecha, hay que ampliar el plan."
        )

    @property
    def texto_badge(self) -> str:
        """Linea corta para el sidebar."""
        if not self.configurado:
            return "Sin plan configurado"
        if self.ilimitado:
            return f"Uso interno · {self.consultas_usadas} consultas este ciclo"
        base = (
            f"{self.consultas_usadas} / {self.consultas_limite} · "
            f"renueva el {self.ciclo_fin.strftime('%d/%m')}"
        )
        if self.tiene_tope_propio:
            return f"Tuyas: {self.usuario_usadas} / {self.tope_usuario} · Empresa: {base}"
        return base


# =====================================================================
# Cache corto
# =====================================================================
#
# Streamlit vuelve a dibujar la pantalla muchas veces por interaccion, y
# el badge se lee en cada una. Sin cache serian varias consultas por
# clic para un numero que cambia una vez por pregunta.
#
# 20 segundos alcanza: registrar_consumo() invalida la entrada al
# escribir, asi que el contador salta al instante despues de cada
# consulta. El TTL solo cubre cambios hechos desde otra sesion.

_CACHE: dict[tuple, tuple[float, EstadoCupo]] = {}
_CACHE_TTL_SEG = 20.0
_LOCK = threading.Lock()


def _ahora() -> float:
    return datetime.now().timestamp()


def invalidar_cache(empresa: str | None = None) -> None:
    with _LOCK:
        if empresa is None:
            _CACHE.clear()
            return
        for clave in [k for k in _CACHE if k[0] == empresa]:
            _CACHE.pop(clave, None)


# =====================================================================
# Lectura
# =====================================================================

_SQL_PLAN = text(
    """
    SELECT  s.empresa,
            s.plan_codigo,
            s.dia_ciclo,
            s.consultas_mes_override,
            s.presupuestos_mes_override,
            s.activo            AS suscripcion_activa,
            p.nombre            AS plan_nombre,
            p.consultas_mes,
            p.presupuestos_mes,
            p.tolerancia_pct
    FROM        suscripciones s
    LEFT JOIN   planes p ON p.codigo = s.plan_codigo
    WHERE       s.empresa = :empresa
    LIMIT 1
    """
)

_SQL_CONSUMO_EMPRESA = text(
    """
    SELECT
        SUM(CASE WHEN origen = 'chat'         THEN 1 ELSE 0 END) AS consultas,
        SUM(CASE WHEN origen = 'presupuestos' THEN 1 ELSE 0 END) AS presupuestos
    FROM consumo
    WHERE empresa = :empresa
      AND ciclo   = :ciclo
      AND computa = 1
    """
)

_SQL_CONSUMO_USUARIO = text(
    """
    SELECT COUNT(*) AS consultas
    FROM consumo
    WHERE empresa = :empresa
      AND usuario = :usuario
      AND ciclo   = :ciclo
      AND computa = 1
      AND origen  = 'chat'
    """
)

_SQL_TOPE_USUARIO = text(
    "SELECT tope_consultas_mes FROM usuarios WHERE usuario = :usuario LIMIT 1"
)


def estado_cupo(empresa: str, usuario: str, usar_cache: bool = True) -> EstadoCupo:
    """
    Estado completo del cupo para la empresa activa y el usuario actual.
    Es lo que consume el badge del sidebar y el control previo a
    responder una consulta.
    """
    empresa = (empresa or "").strip().lower()
    usuario = (usuario or "").strip()
    clave = (empresa, usuario)

    if usar_cache:
        with _LOCK:
            guardado = _CACHE.get(clave)
        if guardado and (_ahora() - guardado[0]) < _CACHE_TTL_SEG:
            return guardado[1]

    hoy = date.today()

    try:
        with get_engine(_base_auth()).connect() as cn:
            plan = cn.execute(_SQL_PLAN, {"empresa": empresa}).mappings().first()

            if not plan or not plan["suscripcion_activa"]:
                inicio = inicio_de_ciclo(hoy, 1)
                estado = EstadoCupo(
                    empresa=empresa,
                    usuario=usuario,
                    ciclo_inicio=inicio,
                    ciclo_fin=fin_de_ciclo(inicio),
                    configurado=False,
                )
                log.warning(
                    "La empresa '%s' no tiene suscripcion activa. "
                    "Se deja pasar la consulta sin descontar cupo.",
                    empresa,
                )
                _guardar_en_cache(clave, estado)
                return estado

            dia = normalizar_dia_ciclo(plan["dia_ciclo"])
            inicio = inicio_de_ciclo(hoy, dia)

            limite = plan["consultas_mes_override"] or plan["consultas_mes"] or 0
            limite_presup = (
                plan["presupuestos_mes_override"]
                if plan["presupuestos_mes_override"] is not None
                else plan["presupuestos_mes"]
            )

            uso = cn.execute(
                _SQL_CONSUMO_EMPRESA, {"empresa": empresa, "ciclo": inicio}
            ).mappings().first()

            uso_usuario = cn.execute(
                _SQL_CONSUMO_USUARIO,
                {"empresa": empresa, "usuario": usuario, "ciclo": inicio},
            ).scalar()

            tope = cn.execute(_SQL_TOPE_USUARIO, {"usuario": usuario}).scalar()

        estado = EstadoCupo(
            empresa=empresa,
            usuario=usuario,
            ciclo_inicio=inicio,
            ciclo_fin=fin_de_ciclo(inicio),
            plan_codigo=plan["plan_codigo"] or "",
            plan_nombre=plan["plan_nombre"] or plan["plan_codigo"] or "",
            configurado=True,
            consultas_usadas=int((uso and uso["consultas"]) or 0),
            consultas_limite=int(limite),
            tolerancia_pct=int(plan["tolerancia_pct"] or 0),
            tope_usuario=int(tope) if tope else None,
            usuario_usadas=int(uso_usuario or 0),
            presupuestos_usados=int((uso and uso["presupuestos"]) or 0),
            presupuestos_limite=int(limite_presup) if limite_presup is not None else None,
        )
        _guardar_en_cache(clave, estado)
        return estado

    except Exception as exc:
        # Si la base de autenticacion no responde, NO se bloquea al
        # usuario. Un problema de infraestructura nuestro no puede
        # dejar al cliente sin poder trabajar.
        log.error("No se pudo leer el cupo de '%s': %s", empresa, exc)
        inicio = inicio_de_ciclo(hoy, 1)
        return EstadoCupo(
            empresa=empresa,
            usuario=usuario,
            ciclo_inicio=inicio,
            ciclo_fin=fin_de_ciclo(inicio),
            configurado=False,
        )


def _guardar_en_cache(clave: tuple, estado: EstadoCupo) -> None:
    with _LOCK:
        _CACHE[clave] = (_ahora(), estado)


def puede_consultar(empresa: str, usuario: str) -> tuple[bool, str]:
    """
    Control previo a responder. Devuelve (permitido, mensaje_si_no).
    El mensaje esta escrito para mostrarselo al usuario tal cual.
    """
    estado = estado_cupo(empresa, usuario)
    if estado.bloqueado:
        return False, estado.motivo_bloqueo
    return True, ""


def puede_presupuestar(empresa: str, usuario: str) -> tuple[bool, str]:
    """Mismo control, contra la bolsa separada del presupuestador."""
    estado = estado_cupo(empresa, usuario)
    if not estado.configurado or estado.presupuestos_limite is None:
        return True, ""
    if estado.presupuestos_limite >= LIMITE_ILIMITADO:
        return True, ""
    tope = int(estado.presupuestos_limite * (1 + estado.tolerancia_pct / 100))
    if estado.presupuestos_usados >= tope:
        return False, (
            f"Se alcanzó el límite de {estado.presupuestos_limite} presupuestos "
            f"del plan para este período. Renueva el "
            f"{estado.ciclo_fin.strftime('%d/%m')}."
        )
    return True, ""


# =====================================================================
# Escritura
# =====================================================================

_SQL_INSERT = text(
    """
    INSERT INTO consumo
        (empresa, usuario, origen, ciclo, computa, exito, modelo,
         tokens_in, tokens_out, tokens_cache_read, tokens_cache_write,
         rondas, costo_usd, duracion_ms, detalle)
    VALUES
        (:empresa, :usuario, :origen, :ciclo, :computa, :exito, :modelo,
         :tokens_in, :tokens_out, :tokens_cache_read, :tokens_cache_write,
         :rondas, :costo_usd, :duracion_ms, :detalle)
    """
)

_SQL_DIA_CICLO = text(
    "SELECT dia_ciclo FROM suscripciones WHERE empresa = :empresa LIMIT 1"
)


def registrar_consumo(
    empresa: str,
    usuario: str,
    origen: str = "chat",
    modelo: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    tokens_cache_read: int = 0,
    tokens_cache_write: int = 0,
    rondas: int = 1,
    duracion_ms: int | None = None,
    exito: bool = True,
    computa: bool | None = None,
    detalle: str | None = None,
) -> bool:
    """
    Graba una consulta. Devuelve True si pudo escribir.

    NUNCA lanza excepcion hacia arriba: si falla el registro, la
    respuesta al usuario ya se generó y no tiene sentido romper la
    pantalla por un problema de medicion. Se loguea y sigue.

    `computa`: si no se especifica, se deduce. El brief y las consultas
    fallidas no descuentan cupo.
    """
    empresa = (empresa or "").strip().lower()
    usuario = (usuario or "").strip()

    if origen not in ("chat", "presupuestos", "brief", "otro"):
        origen = "otro"

    if computa is None:
        computa = bool(exito) and origen in ("chat", "presupuestos")

    try:
        base = _base_auth()
        with get_engine(base).begin() as cn:
            dia = cn.execute(_SQL_DIA_CICLO, {"empresa": empresa}).scalar()
            ciclo = inicio_de_ciclo(date.today(), normalizar_dia_ciclo(dia))

            cn.execute(
                _SQL_INSERT,
                {
                    "empresa": empresa,
                    "usuario": usuario,
                    "origen": origen,
                    "ciclo": ciclo,
                    "computa": 1 if computa else 0,
                    "exito": 1 if exito else 0,
                    "modelo": modelo,
                    "tokens_in": max(0, int(tokens_in or 0)),
                    "tokens_out": max(0, int(tokens_out or 0)),
                    "tokens_cache_read": max(0, int(tokens_cache_read or 0)),
                    "tokens_cache_write": max(0, int(tokens_cache_write or 0)),
                    "rondas": max(1, int(rondas or 1)),
                    "costo_usd": costo_estimado(
                        modelo, tokens_in, tokens_out,
                        tokens_cache_read, tokens_cache_write,
                    ),
                    "duracion_ms": int(duracion_ms) if duracion_ms else None,
                    "detalle": (detalle or None) and str(detalle)[:255],
                },
            )
        invalidar_cache(empresa)
        return True

    except Exception as exc:
        log.error(
            "No se pudo registrar el consumo de %s/%s: %s", empresa, usuario, exc
        )
        return False


# =====================================================================
# Panel de administracion
# =====================================================================

_SQL_RESUMEN_USUARIOS = text(
    """
    SELECT  c.usuario,
            u.nombre,
            SUM(CASE WHEN c.origen = 'chat'         AND c.computa = 1 THEN 1 ELSE 0 END) AS consultas,
            SUM(CASE WHEN c.origen = 'presupuestos' AND c.computa = 1 THEN 1 ELSE 0 END) AS presupuestos,
            SUM(c.tokens_in + c.tokens_out) AS tokens,
            ROUND(SUM(c.costo_usd), 4)      AS costo_usd,
            u.tope_consultas_mes            AS tope,
            MAX(c.creado_el)                AS ultima
    FROM        consumo c
    LEFT JOIN   usuarios u ON u.usuario = c.usuario
    WHERE       c.empresa = :empresa
      AND       c.ciclo   = :ciclo
    GROUP BY    c.usuario, u.nombre, u.tope_consultas_mes
    ORDER BY    consultas DESC
    """
)


def detalle_por_usuario(empresa: str) -> list[dict]:
    """Consumo del ciclo vigente desglosado por usuario."""
    estado = estado_cupo(empresa, "", usar_cache=False)
    try:
        with get_engine(_base_auth()).connect() as cn:
            filas = cn.execute(
                _SQL_RESUMEN_USUARIOS,
                {"empresa": (empresa or "").strip().lower(),
                 "ciclo": estado.ciclo_inicio},
            ).mappings().all()
        return [dict(f) for f in filas]
    except Exception as exc:
        log.error("No se pudo leer el detalle de consumo: %s", exc)
        return []
