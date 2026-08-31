import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from core.panel_access import crear_enlace, revocar, LEASE_KEY


class PanelAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ('leases', 'tickets'):
            (self.root / name).mkdir()
        self.env = patch.dict(os.environ, {
            'KYBER_PANEL_STATE': str(self.root),
            'KYBER_PANEL_URL': 'https://kyber.example:8443/panel-sync',
        })
        self.env.start()
        self.user = {'id': 1, 'rol': 'admin', 'empresas': ['ekaru']}
        self.session = {}

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_ticket_only_in_fragment_and_expires(self):
        link = crear_enlace(self.user, 'ekaru', self.session, time.time() + 3600)
        parsed = urlsplit(link)
        self.assertFalse(parsed.query)
        self.assertEqual(parsed.path, '/panel-sync/ekaru/entrar')
        record = json.loads((self.root/'tickets'/hashlib.sha256(parsed.fragment.encode()).hexdigest()).read_text())
        self.assertEqual(record['company'], 'ekaru')
        self.assertLessEqual(record['expires'], time.time() + 300)

    def test_rejects_non_admin_cross_company_and_path(self):
        for user, company in [({**self.user, 'rol': 'gerencia'}, 'ekaru'),
                              (self.user, 'ejapo'), (self.user, '../ekaru'),
                              ({**self.user, 'debe_cambiar_password': True}, 'ekaru')]:
            with self.assertRaises(PermissionError):
                crear_enlace(user, company, self.session, time.time() + 3600)
        self.assertFalse(list((self.root/'tickets').iterdir()))

    def test_https_required(self):
        with patch.dict(os.environ, {'KYBER_PANEL_URL':'http://kyber.example/panel-sync'}):
            with self.assertRaises(ValueError):
                crear_enlace(self.user, 'ekaru', self.session, time.time() + 3600)

    def test_revokes_on_logout(self):
        crear_enlace(self.user, 'ekaru', self.session, time.time() + 3600)
        lease = self.root/'leases'/self.session[LEASE_KEY]
        self.assertTrue(lease.exists())
        revocar(self.session)
        self.assertFalse(lease.exists())

    def test_expired_session_cannot_issue_ticket(self):
        with self.assertRaises(PermissionError):
            crear_enlace(self.user, 'ekaru', self.session, time.time() - 1)
