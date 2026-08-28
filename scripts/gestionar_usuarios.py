"""
scripts/gestionar_usuarios.py
==============================
Administracion de usuarios de Conepasa IA desde una ventana de comandos.

Se usa para crear el PRIMER usuario administrador (cuando todavia no hay
nadie que pueda entrar al sistema) y como respaldo para restablecer una
contrasena si alguien queda afuera.

Forma facil de ejecutarlo: doble clic en  gestionar_usuarios.bat

La contrasena nunca se muestra en pantalla mientras se tipea, y nunca se
guarda en claro: en la base queda solamente el hash scrypt.
"""

import getpass
import pathlib
import sys

# Permite ejecutar este archivo desde la carpeta scripts/ y que igual
# encuentre el paquete core/ que esta un nivel mas arriba.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from core import auth  # noqa: E402
from core.config import settings  # noqa: E402


LINEA = "=" * 62


def encabezado(titulo: str) -> None:
    print()
    print(LINEA)
    print(f" {titulo}")
    print(LINEA)


def pedir(mensaje: str, obligatorio: bool = True, por_defecto: str = "") -> str:
    while True:
        sufijo = f" [{por_defecto}]" if por_defecto else ""
        valor = input(f"{mensaje}{sufijo}: ").strip()
        if not valor and por_defecto:
            return por_defecto
        if valor or not obligatorio:
            return valor
        print("   Este dato es obligatorio.")


def pedir_password() -> str:
    while True:
        primera = getpass.getpass("Contraseña (no se ve al tipear): ")
        segunda = getpass.getpass("Repetila: ")
        error = auth.validar_password_nueva(primera, segunda)
        if error:
            print(f"   {error}")
            continue
        return primera


def verificar_conexion() -> bool:
    conectado, mensaje = auth.probar_conexion()
    if conectado:
        print(f"Conectado a {settings.auth_database} como {settings.auth_mysql_user}.")
        return True
    print()
    print("NO SE PUDO CONECTAR A LA BASE DE USUARIOS.")
    print()
    print("Revisá estas cuatro líneas en el archivo .env:")
    print("   AUTH_DATABASE, AUTH_MYSQL_USER, AUTH_MYSQL_PASSWORD, AUTH_SESSION_HORAS")
    print()
    print(f"Detalle técnico: {mensaje}")
    return False


# ---------------------------------------------------------------------
# Operaciones
# ---------------------------------------------------------------------

def listar_usuarios() -> None:
    encabezado("USUARIOS CARGADOS")
    with auth.get_auth_engine().connect() as conexion:
        filas = conexion.execute(
            text(
                "SELECT usuario, nombre, rol, empresas, sucursales, activo, "
                "debe_cambiar_password, ultimo_acceso "
                "FROM usuarios ORDER BY usuario"
            )
        ).mappings().all()

    if not filas:
        print("Todavía no hay ningún usuario cargado.")
        print("Usá la opción 2 para crear el primero.")
        return

    for fila in filas:
        estado = "activo" if fila["activo"] else "DESACTIVADO"
        pendiente = " · debe cambiar clave" if fila["debe_cambiar_password"] else ""
        print()
        print(f"  {fila['usuario']}  ({fila['nombre']})")
        print(f"     rol: {fila['rol']}   estado: {estado}{pendiente}")
        print(f"     empresas: {fila['empresas'] or 'todas'}")
        print(f"     sucursales: {fila['sucursales'] or 'todas'}")
        print(f"     último acceso: {fila['ultimo_acceso'] or 'nunca'}")


def crear_usuario() -> None:
    encabezado("CREAR USUARIO")

    usuario = pedir("Nombre de acceso (sin espacios, ej: esteban)").lower()

    with auth.get_auth_engine().connect() as conexion:
        existe = conexion.execute(
            text("SELECT 1 FROM usuarios WHERE usuario = :usuario"),
            {"usuario": usuario},
        ).first()
    if existe:
        print(f"Ya existe un usuario '{usuario}'. Usá la opción 3 para cambiarle la clave.")
        return

    nombre = pedir("Nombre y apellido")
    email = pedir("Email (opcional, Enter para saltear)", obligatorio=False)

    print()
    print("Roles disponibles:")
    for indice, rol in enumerate(auth.ROLES, start=1):
        print(f"   {indice}. {rol:<14} {auth.ROLES_DESCRIPCION[rol]}")
    while True:
        eleccion = pedir("Número de rol", por_defecto="2")
        if eleccion.isdigit() and 1 <= int(eleccion) <= len(auth.ROLES):
            rol = auth.ROLES[int(eleccion) - 1]
            break
        print("   Elegí un número de la lista.")

    print()
    print("Empresas: escribí las claves separadas por coma (ekaru, ejapo).")
    print("Dejalo vacío para que vea todas las empresas de esta instalación.")
    empresas = pedir("Empresas", obligatorio=False).lower().replace(" ", "")

    print()
    print("Sucursales: nombres exactos separados por coma.")
    print("Dejalo vacío para que vea todas.")
    sucursales = pedir("Sucursales", obligatorio=False)

    print()
    print("Ahora la contraseña provisoria.")
    print("El usuario va a estar obligado a cambiarla en su primer ingreso,")
    print("así que después de eso vos ya no vas a saber cuál es.")
    password = pedir_password()

    with auth.get_auth_engine().begin() as conexion:
        conexion.execute(
            text(
                "INSERT INTO usuarios "
                "(usuario, nombre, email, password_hash, rol, empresas, sucursales, "
                " activo, debe_cambiar_password) "
                "VALUES (:usuario, :nombre, :email, :hash, :rol, :empresas, :sucursales, 1, 1)"
            ),
            {
                "usuario": usuario,
                "nombre": nombre,
                "email": email or None,
                "hash": auth.hashear_password(password),
                "rol": rol,
                "empresas": empresas,
                "sucursales": sucursales or None,
            },
        )

    auth.registrar_acceso(usuario, "usuario_creado", f"rol={rol}")
    print()
    print(f"Listo. Usuario '{usuario}' creado con rol '{rol}'.")


def restablecer_password() -> None:
    encabezado("RESTABLECER CONTRASEÑA")
    usuario = pedir("Nombre de acceso").lower()

    with auth.get_auth_engine().connect() as conexion:
        existe = conexion.execute(
            text("SELECT nombre FROM usuarios WHERE usuario = :usuario"),
            {"usuario": usuario},
        ).mappings().first()
    if not existe:
        print(f"No existe ningún usuario '{usuario}'.")
        return

    print(f"Restableciendo la clave de {existe['nombre']}.")
    password = pedir_password()

    with auth.get_auth_engine().begin() as conexion:
        conexion.execute(
            text(
                "UPDATE usuarios SET password_hash = :hash, debe_cambiar_password = 1, "
                "intentos_fallidos = 0, bloqueado_hasta = NULL WHERE usuario = :usuario"
            ),
            {"hash": auth.hashear_password(password), "usuario": usuario},
        )

    auth.registrar_acceso(usuario, "usuario_modificado", "Contraseña restablecida")
    print()
    print("Listo. Va a tener que cambiarla en su próximo ingreso.")


def cambiar_estado() -> None:
    encabezado("ACTIVAR O DESACTIVAR USUARIO")
    usuario = pedir("Nombre de acceso").lower()

    with auth.get_auth_engine().connect() as conexion:
        fila = conexion.execute(
            text("SELECT nombre, activo FROM usuarios WHERE usuario = :usuario"),
            {"usuario": usuario},
        ).mappings().first()
    if not fila:
        print(f"No existe ningún usuario '{usuario}'.")
        return

    nuevo_estado = 0 if fila["activo"] else 1
    accion = "activar" if nuevo_estado else "desactivar"
    confirmar = pedir(f"Confirmás {accion} a {fila['nombre']}? (si/no)", por_defecto="no")
    if confirmar.lower() not in ("si", "sí", "s"):
        print("Cancelado.")
        return

    with auth.get_auth_engine().begin() as conexion:
        conexion.execute(
            text("UPDATE usuarios SET activo = :activo WHERE usuario = :usuario"),
            {"activo": nuevo_estado, "usuario": usuario},
        )

    auth.registrar_acceso(
        usuario,
        "usuario_modificado" if nuevo_estado else "usuario_desactivado",
        f"Estado cambiado a {'activo' if nuevo_estado else 'inactivo'}",
    )
    print(f"Listo. Usuario {'activado' if nuevo_estado else 'desactivado'}.")


def desbloquear() -> None:
    encabezado("DESBLOQUEAR POR INTENTOS FALLIDOS")
    usuario = pedir("Nombre de acceso").lower()
    with auth.get_auth_engine().begin() as conexion:
        resultado = conexion.execute(
            text(
                "UPDATE usuarios SET intentos_fallidos = 0, bloqueado_hasta = NULL "
                "WHERE usuario = :usuario"
            ),
            {"usuario": usuario},
        )
    if resultado.rowcount:
        print("Listo, ya puede volver a intentar.")
    else:
        print(f"No existe ningún usuario '{usuario}'.")


def ver_accesos() -> None:
    encabezado("ÚLTIMOS 30 EVENTOS DE ACCESO")
    eventos = auth.accesos_recientes(30)
    if not eventos:
        print("Todavía no hay eventos registrados.")
        return
    for evento in eventos:
        detalle = f" · {evento['detalle']}" if evento["detalle"] else ""
        print(f"  {evento['creado_el']}  {evento['usuario']:<20} {evento['evento']}{detalle}")


# ---------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------

OPCIONES = {
    "1": ("Ver usuarios cargados", listar_usuarios),
    "2": ("Crear un usuario nuevo", crear_usuario),
    "3": ("Restablecer la contraseña de alguien", restablecer_password),
    "4": ("Activar o desactivar un usuario", cambiar_estado),
    "5": ("Desbloquear por intentos fallidos", desbloquear),
    "6": ("Ver últimos accesos", ver_accesos),
    "0": ("Salir", None),
}


def main() -> int:
    encabezado("CONEPASA IA · ADMINISTRACIÓN DE USUARIOS")
    if not verificar_conexion():
        input("\nEnter para cerrar...")
        return 1

    while True:
        print()
        print(LINEA)
        for clave, (titulo, _) in OPCIONES.items():
            print(f"  {clave}. {titulo}")
        print(LINEA)
        eleccion = input("Opción: ").strip()

        if eleccion == "0":
            print("Hasta luego.")
            return 0

        entrada = OPCIONES.get(eleccion)
        if not entrada:
            print("Opción no válida.")
            continue

        try:
            entrada[1]()
        except Exception as error:
            print()
            print(f"Ocurrió un error: {error}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelado.")
        sys.exit(1)
