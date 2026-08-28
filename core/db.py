from functools import lru_cache
from urllib.parse import quote_plus

from sqlalchemy import create_engine, inspect, text

from core.config import settings


# Registro de los motores que se fueron creando. lru_cache no expone lo
# que guarda, asi que se anotan aca para poder cerrarlos en reset_engines.
_motores_creados: list = []


@lru_cache(maxsize=8)
def get_engine(database: str):
    """
    Motor de conexion a una base. UNO SOLO por base, reutilizado.

    POR QUE EL CACHE
    ----------------
    Esta funcion se llama en cada operacion: cada listar_tablas, cada
    ver_columnas, cada ejecutar_sql y cada ping. Sin cache, cada una de
    esas llamadas creaba un motor nuevo con su propio pool de
    conexiones, lo usaba una vez y lo tiraba.

    Una sola pregunta al agente encadena entre 3 y 8 herramientas, o sea
    entre 3 y 8 conexiones nuevas a MySQL donde alcanzaba con una. Y
    ping() corre en CADA recarga de pantalla de Streamlit: cada clic,
    cada tecla en el chat.

    Con el cache hay un motor por base, creado la primera vez y
    reutilizado siempre. El pool interno de SQLAlchemy se encarga de
    mantener y reciclar las conexiones, que es exactamente para lo que
    esta hecho.

    maxsize=8 alcanza de sobra: hoy son dos bases de negocio, y aun con
    varios clientes en la misma instalacion no se llega a ocho.

    OJO AL CAMBIAR CREDENCIALES
    ---------------------------
    El motor queda vivo mientras corra el proceso. Si cambias la clave
    de MySQL en el .env, la app va a seguir usando la anterior hasta que
    la reinicies. Como todo cambio en core/ ya exige reiniciar, en la
    practica no cambia nada, pero si alguna vez una credencial nueva
    "no toma", la causa es esta.
    """
    user = quote_plus(settings.mysql_user)
    password = quote_plus(settings.mysql_password)
    url = (
        f"mysql+pymysql://{user}:{password}@{settings.mysql_host}:"
        f"{settings.mysql_port}/{database}?charset=utf8mb4"
    )
    engine = create_engine(
        url,
        # pool_pre_ping verifica que la conexion siga viva antes de
        # usarla. Es lo que hace seguro reutilizar el motor: si MySQL
        # cerro la conexion por inactividad, SQLAlchemy abre otra sola
        # en vez de fallar.
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={"read_timeout": 30, "write_timeout": 30},
    )
    _motores_creados.append(engine)
    return engine


def reset_engines() -> None:
    """
    Cierra y olvida todos los motores cacheados.

    Fuerza una reconexion sin reiniciar la aplicacion. Util despues de
    cambiar credenciales en el .env o de reiniciar MySQL.

    Cierra las conexiones abiertas antes de descartar los motores, para
    no dejarlas colgadas del lado del servidor de base de datos.
    """
    for engine in list(_motores_creados):
        try:
            engine.dispose()
        except Exception:
            pass
    _motores_creados.clear()
    get_engine.cache_clear()


def ping(database: str):
    try:
        with get_engine(database).connect() as connection:
            connection.execute(text("SELECT 1"))
        return True, "Conectado"
    except Exception as exc:
        return False, str(exc)


def list_tables(database: str) -> list[str]:
    """
    Devuelve tablas Y VISTAS de la base.

    IMPORTANTE: las vistas tienen que estar incluidas. En SQLAlchemy,
    get_table_names() devuelve unicamente tablas fisicas; las vistas se
    piden aparte con get_view_names().

    Cuando faltaban las vistas, el agente no veia v_ventas_jornada ni
    ninguna de las v_*, avisaba "no encuentro esa vista" y resolvia la
    pregunta consultando la tabla `ventas` directamente. Eso devuelve
    cifras distintas, porque las vistas aplican el corte de las 03:00
    para atribuir correctamente la jornada de trabajo. Un mismo dia
    llego a dar 53 comprobantes por la vista y 48 por la tabla cruda.

    Las vistas son la interfaz correcta para consultar; las tablas
    crudas son el detalle interno.
    """
    inspector = inspect(get_engine(database))
    tablas = inspector.get_table_names()
    vistas = inspector.get_view_names()
    # Se unifican en una sola lista para no cambiar lo que espera el
    # agente. Los nombres de las vistas ya se distinguen solos por el
    # prefijo v_.
    return sorted(set(tablas) | set(vistas))


def table_columns(database: str, table_name: str) -> list[dict]:
    """Columnas de una tabla o de una vista. Funciona igual con las dos."""
    safe_table = str(table_name).replace("`", "").strip()
    columns = inspect(get_engine(database)).get_columns(safe_table)
    return [{"nombre": c["name"], "tipo": str(c["type"])} for c in columns]
