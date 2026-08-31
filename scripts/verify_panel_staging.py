"""Read-only integration checks, executed as kyber with the TEST environment.

No login passwords, tickets, cookies, rows or HTML are printed. A short-lived
test handoff for an existing enabled test administrator is revoked in finally.
The only mutation-shaped requests use an invalid view and MUST fail at CSRF.
"""
from __future__ import annotations

import http.client
from http.cookies import SimpleCookie
import os
from pathlib import Path
import socket
import ssl
import sys
import time
from urllib.parse import urlencode, urlsplit

import pymysql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.panel_access import crear_enlace, revocar


class LoopbackHTTPS(http.client.HTTPSConnection):
    def connect(self):
        self.sock = ssl.create_default_context().wrap_socket(
            socket.create_connection(('127.0.0.1', self.port), timeout=10),
            server_hostname=self.host,
        )


def main():
    assert os.getenv('AUTH_DATABASE') == 'conepasa_auth_pruebas'
    base = urlsplit(os.environ['KYBER_PANEL_URL'])
    origin = f'{base.scheme}://{base.netloc}'
    connection = pymysql.connect(host='127.0.0.1', port=3306,
        user=os.environ['AUTH_MYSQL_USER'], password=os.environ['AUTH_MYSQL_PASSWORD'],
        database='conepasa_auth_pruebas', cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5, read_timeout=5)
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, empresas FROM usuarios WHERE activo=1 AND rol='admin' AND debe_cambiar_password=0")
        admins = cursor.fetchall()
    connection.close()

    for company in ('ekaru', 'ejapo'):
        admin = next(a for a in admins if not (a['empresas'] or '').strip()
                     or company in [x.strip() for x in a['empresas'].split(',')])
        state = {}
        cookies = {}

        def request(method, path, data=None):
            client = LoopbackHTTPS(base.hostname, base.port or 443, timeout=10)
            headers = {'Origin': origin}
            if cookies:
                headers['Cookie'] = '; '.join(f'{k}={v}' for k, v in cookies.items())
            body = urlencode(data) if data is not None else None
            if body is not None:
                headers['Content-Type'] = 'application/x-www-form-urlencoded'
            client.request(method, path, body, headers)
            response = client.getresponse()
            status = response.status
            for name, value in response.getheaders():
                if name.lower() == 'set-cookie':
                    parsed = SimpleCookie(); parsed.load(value)
                    for key, item in parsed.items():
                        cookies[key] = item.value
                        assert item['secure'] and item['httponly'] and item['samesite'] == 'Strict'
            content = response.read().decode('utf-8', errors='replace')
            client.close()
            return status, content

        path = base.path + '/' + company
        try:
            assert request('GET', path + '/index.php')[0] == 403
            link = crear_enlace({'id': admin['id'], 'rol': 'admin', 'empresas': [company]},
                                company, state, time.time() + 60)
            token = urlsplit(link).fragment
            assert request('POST', path + '/entrar', {'ticket': token})[0] == 303
            status, page = request('GET', path + '/index.php')
            assert status == 200 and 'Vistas disponibles' in page and 'Sincronizar todo' in page
            status, data = request('GET', path + '/sync.php?action=status')
            import json
            payload = json.loads(data)
            assert status == 200 and payload['ok'] and payload['vistas']
            assert request('POST', path + '/sync.php', {'action':'enqueue', 'vista':'__TEST_NOT_A_VIEW__'})[0] == 403
            assert request('GET', path + '/sync.php?action=enqueue&vista=__TEST_NOT_A_VIEW__')[0] == 403
            assert request('GET', path + '/config.txt')[0] == 404
            assert request('GET', path + '/worker.php')[0] == 404
            status, settings = request('GET', path + '/configuracion.php')
            assert status == 200 and '<input' not in settings
            assert request('POST', path + '/entrar', {'ticket': token})[0] == 403
            revocar(state)
            assert request('GET', path + '/index.php')[0] == 403
            print(f'{company}: AUTH, PANEL, STATUS, CSRF, FILE_DENIAL, REPLAY, LOGOUT OK')
        finally:
            revocar(state)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Tracebacks could include URLs/tickets/data; emit only the type.
        print(f'INTEGRATION_FAILED: {type(exc).__name__}', file=sys.stderr)
        sys.exit(1)
