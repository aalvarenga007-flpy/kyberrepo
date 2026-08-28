"""
probar_consumo.py
=================
Prueba de instalacion del modulo de consumo. Se corre una sola vez,
despues de copiar core/consumo.py.

Verifica tres cosas:
  1. Que el modulo importe y encuentre la base de autenticacion.
  2. Que pueda LEER el plan y el consumo de cada empresa.
  3. Que pueda ESCRIBIR en la tabla `consumo`.

La fila de prueba que escribe va con origen='otro' y computa=0, asi que
no le descuenta cupo a nadie ni ensucia los contadores del cliente.

Se puede borrar este archivo despues de la prueba.
"""

from datetime import date

from sqlalchemy import text

from core.consumo import (
    _base_auth,
    estado_cupo,
    inicio_de_ciclo,
    fin_de_ciclo,
    registrar_consumo,
    detalle_por_usuario,
)
from core.db import get_engine


def linea(titulo=""):
    print("\n" + "=" * 62)
    if titulo:
        print(titulo)
        print("=" * 62)


def main():
    linea("1. CONEXION")
    base = _base_auth()
    print(f"   Base de autenticacion: {base}")

    with get_engine(base).connect() as cn:
        empresas = [
            f["empresa"]
            for f in cn.execute(
                text("SELECT empresa FROM suscripciones WHERE activo = 1 ORDER BY empresa")
            ).mappings()
        ]
        usuario = cn.execute(
            text("SELECT usuario FROM usuarios WHERE activo = 1 ORDER BY id LIMIT 1")
        ).scalar()

    print(f"   Empresas con suscripcion activa: {', '.join(empresas) or '(ninguna)'}")
    print(f"   Usuario de prueba: {usuario}")

    if not empresas:
        print("\n   ERROR: no hay suscripciones activas. Revisar la tabla.")
        return
    if not usuario:
        print("\n   ERROR: no hay usuarios activos. Revisar la tabla.")
        return

    linea("2. LECTURA DEL CUPO")
    for empresa in empresas:
        e = estado_cupo(empresa, usuario, usar_cache=False)
        print(f"\n   [{empresa}]")
        print(f"   Plan            : {e.plan_nombre or '(sin plan)'}")
        print(f"   Configurado     : {'si' if e.configurado else 'NO'}")
        print(f"   Ciclo           : {e.ciclo_inicio} -> {e.ciclo_fin}")
        print(f"   Consultas       : {e.consultas_usadas} / {e.consultas_limite}")
        print(f"   Ilimitado       : {'si' if e.ilimitado else 'no'}")
        print(f"   Nivel           : {e.nivel}")
        print(f"   Badge           : {e.texto_badge}")

    linea("3. CALCULO DE CICLO")
    hoy = date.today()
    for dia in (1, 15, 28):
        ini = inicio_de_ciclo(hoy, dia)
        print(f"   dia_ciclo={dia:>2}  ->  {ini}  hasta  {fin_de_ciclo(ini)}")

    linea("4. ESCRITURA (fila de prueba, no descuenta cupo)")
    ok = registrar_consumo(
        empresa=empresas[0],
        usuario=usuario,
        origen="otro",
        modelo="claude-haiku-4-5-20251001",
        tokens_in=1000,
        tokens_out=200,
        computa=False,
        detalle="prueba de instalacion",
    )
    print(f"   Registro escrito: {'SI' if ok else 'NO (revisar permisos de MySQL)'}")

    if ok:
        with get_engine(base).connect() as cn:
            fila = cn.execute(
                text(
                    "SELECT id, empresa, usuario, origen, ciclo, computa, "
                    "modelo, tokens_in, tokens_out, costo_usd "
                    "FROM consumo ORDER BY id DESC LIMIT 1"
                )
            ).mappings().first()
        print("   Ultima fila en la tabla:")
        for clave, valor in dict(fila).items():
            print(f"      {clave:<18} {valor}")

    linea("5. DETALLE POR USUARIO")
    filas = detalle_por_usuario(empresas[0])
    if not filas:
        print("   (todavia sin consumo computado en el ciclo, es lo esperado)")
    for f in filas:
        print(f"   {f['usuario']:<20} {f['consultas']} consultas  "
              f"USD {f['costo_usd']}")

    linea("LISTO")
    print("   Si los cinco pasos salieron sin error, el modulo esta bien")
    print("   instalado y se puede enganchar al agente.\n")


if __name__ == "__main__":
    main()
