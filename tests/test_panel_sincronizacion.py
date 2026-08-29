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
        self.assertEqual(app.metric[0].label, "Última actualización de Ventas")
        self.assertEqual(app.metric[3].value, "Cada 1 h")
        self.assertEqual(app.selectbox[0].value, "Ventas")
        self.assertTrue(any("Actualizar solo Ventas" in button.label for button in app.button))

    def test_explica_estado_no_disponible_sin_tecnicismos(self):
        app = AppTest.from_string(
            r'''
from core import panel_sincronizacion as panel
from core.sync_monitor import SyncMonitorError

def estado_no_disponible(company):
    raise SyncMonitorError("Detalle técnico reservado para soporte.")

panel._estado_cacheado = estado_no_disponible
panel.render("ekaru", "Ekarú Gastronomía")
'''
        )
        app.run(timeout=20)

        self.assertFalse(list(app.exception))
        self.assertTrue(any("automáticamente cada hora" in item.value for item in app.info))
        self.assertTrue(any("todavía no está habilitada" in item.value for item in app.warning))
        boton = next(
            button for button in app.button
            if "Actualizar datos ahora" in button.label
        )
        self.assertTrue(boton.disabled)


if __name__ == "__main__":
    unittest.main()
