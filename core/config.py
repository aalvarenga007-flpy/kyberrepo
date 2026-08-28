from dataclasses import dataclass
import os

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Conepasa IA")

    mysql_host: str = os.getenv("MYSQL_HOST", "127.0.0.1")
    mysql_port: int = int(os.getenv("MYSQL_PORT", "3306"))
    mysql_user: str = os.getenv("MYSQL_USER", "root")
    mysql_password: str = os.getenv("MYSQL_PASSWORD", "")

    ekaru_database: str = os.getenv("EKARU_DATABASE", "ekaru_gastronomia_bi")
    ejapo_database: str = os.getenv("EJAPO_DATABASE", "ejapo_sanjose_bi")

    # --- Identificacion de la instalacion -----------------------------------
    #
    # Que cliente es esta copia. Tiene que coincidir con el cliente_id
    # cargado en la tabla `clientes` de conepasa_auth: de ahi sale si la
    # suscripcion esta al dia, en gracia o cortada.
    #
    # Es la unica linea del .env que cambia entre una instalacion y otra
    # en lo que hace al control comercial. El codigo es identico.
    cliente_id: str = os.getenv("CLIENTE_ID", "").strip().lower()

    # --- Autenticacion (Fase 0) --------------------------------------------
    #
    # Los usuarios viven en una base separada de las bases del negocio, con
    # su propio usuario MySQL. Ese usuario tiene permiso de escritura solo
    # sobre conepasa_auth y ningun acceso a los datos comerciales.
    auth_database: str = os.getenv("AUTH_DATABASE", "conepasa_auth")
    auth_mysql_user: str = os.getenv("AUTH_MYSQL_USER", "conepasa_auth")
    auth_mysql_password: str = os.getenv("AUTH_MYSQL_PASSWORD", "")
    auth_session_horas: int = int(os.getenv("AUTH_SESSION_HORAS", "12"))

    sql_max_rows: int = int(os.getenv("SQL_MAX_ROWS", "500"))
    max_tool_rounds: int = int(os.getenv("MAX_TOOL_ROUNDS", "14"))

    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

    # --- Ruteo de modelo por complejidad de la pregunta ---------------------
    #
    # Las preguntas de una sola tabla ("cuanto vendimos ayer") no necesitan el
    # modelo grande. El modelo chico cuesta bastante menos y responde igual de
    # bien ese tipo de consulta. Si tropieza, el agente reintenta solo con el
    # modelo grande, asi que el peor caso es gastar un poco de mas, nunca
    # entregar una respuesta peor.
    #
    # ROUTING_MODO acepta tres valores:
    #   activo   -> las preguntas simples van al modelo chico de verdad.
    #   sombra   -> todo sigue en el modelo grande, pero se registra en el CSV
    #               que habria decidido el ruteo. Sirve para medir sin riesgo.
    #   apagado  -> comportamiento original, sin ruteo y sin registro.
    anthropic_model_simple: str = os.getenv(
        "ANTHROPIC_MODEL_SIMPLE", "claude-haiku-4-5-20251001"
    )
    routing_modo: str = os.getenv("ROUTING_MODO", "activo").strip().lower()

    # Resumen diario (alertas por email / ventana local).
    alert_send_email: bool = os.getenv("ALERT_SEND_EMAIL", "true").strip().lower() == "true"
    alert_show_window: bool = os.getenv("ALERT_SHOW_WINDOW", "true").strip().lower() == "true"
    smtp_host: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    alert_email_to: str = os.getenv("ALERT_EMAIL_TO", "")


settings = Settings()
