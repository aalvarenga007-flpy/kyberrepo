import unittest

from streamlit.testing.v1 import AppTest


PANEL_APP = r'''
from core import panel_sincronizacion as panel

panel._estado_cacheado = lambda company: {
    "ok": True,
    "server_time": "2026-08-29 11:00:00",
    "worker_last_run": "2026-08-29 10:01:00",
    "auto_sync_hours": 1,
    "resync_days": 5,
    "views": [
        {
            "name": "Ventas",
            "last_sync_status": "ok",
            "last_sync_end": "2026-08-29 10:00:00",
            "total_records_local": 915030,
            "last_records_inserted": 120,
            "last_records_updated": 50,
            "queue": None,
        }
    ],
    "sales_history": [],
}

panel.render("ekaru", "Ekarú Gastronomía")
'''


class PanelSincronizacionTests(unittest.TestCase):
    def test_renderiza_estado_y_accion_manual(self):
        app = AppTest.from_string(PANEL_APP)
        app.run(timeout=20)

        self.assertFalse(list(app.exception))
        self.assertEqual(app.metric[0].label, "Última sync de Ventas")
        self.assertEqual(app.metric[3].value, "Cada 1 h")
        self.assertEqual(app.selectbox[0].value, "Ventas")
        self.assertTrue(any("Sincronizar Ventas" in button.label for button in app.button))


if __name__ == "__main__":
    unittest.main()
