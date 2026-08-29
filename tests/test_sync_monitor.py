import json
import subprocess
import unittest
from unittest.mock import patch

from core.auth import PERMISOS
from core import sync_monitor


class SyncMonitorTests(unittest.TestCase):
    def test_solo_admin_recibe_permiso_de_sincronizacion(self):
        self.assertTrue(PERMISOS["admin"]["administra_sincronizacion"])
        for rol in ("gerencia", "operacion", "presupuestos"):
            self.assertFalse(PERMISOS[rol]["administra_sincronizacion"])

    def test_rechaza_empresa_fuera_de_lista_cerrada(self):
        with self.assertRaises(sync_monitor.SyncMonitorError):
            sync_monitor._empresa_valida("../../otra")

    @patch("core.sync_monitor.subprocess.run")
    @patch("core.sync_monitor._rutas_control", return_value=("/usr/bin/php", "/sync/control.php"))
    def test_parsea_estado_json_sin_shell(self, _rutas, ejecutar):
        ejecutar.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps({"ok": True, "views": []}), stderr=""
        )

        payload = sync_monitor.obtener_estado("EKARU")

        self.assertTrue(payload["ok"])
        comando = ejecutar.call_args.args[0]
        self.assertEqual(comando, ["/usr/bin/php", "/sync/control.php", "status"])
        self.assertFalse(ejecutar.call_args.kwargs["shell"])

    @patch("core.sync_monitor._iniciar_worker")
    @patch("core.sync_monitor._ejecutar_control")
    def test_sincronizacion_manual_encola_y_despierta_worker(self, control, iniciar):
        control.return_value = {"ok": True, "queued": 1}

        resultado = sync_monitor.sincronizar_ahora("ejapo", "Ventas")

        self.assertEqual(resultado["queued"], 1)
        control.assert_called_once_with("ejapo", "enqueue", "Ventas")
        iniciar.assert_called_once_with("ejapo")

    @patch("core.sync_monitor._iniciar_worker")
    @patch("core.sync_monitor._ejecutar_control")
    def test_sincronizar_todo_usa_accion_cerrada(self, control, iniciar):
        control.return_value = {"ok": True, "queued": 20}

        sync_monitor.sincronizar_ahora("ekaru")

        control.assert_called_once_with("ekaru", "enqueue-all")
        iniciar.assert_called_once_with("ekaru")


if __name__ == "__main__":
    unittest.main()
