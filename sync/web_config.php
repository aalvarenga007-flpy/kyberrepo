<?php
if (!defined('KYBER_WEB_PANEL')) { http_response_code(404); exit; }
require_once __DIR__ . '/src/Config.php';
$config = new Config(BI_ROOT . '/config.txt');
header('Content-Type: text/html; charset=utf-8');
?>
<!doctype html><html lang="es"><meta charset="utf-8"><title>Configuración del sincronizador</title>
<body style="background:#101116;color:#eee;font:16px system-ui;max-width:760px;margin:60px auto;padding:24px">
<a href="index.php" style="color:#93a6ff">← Volver al panel</a>
<h1>Configuración del sincronizador</h1>
<p>Empresa: <strong><?= htmlspecialchars($config->getAppName(), ENT_QUOTES, 'UTF-8') ?></strong></p>
<p>Actualizar automáticamente cada <?= (int)$config->get('auto_sync_hours', 24) ?> horas.</p>
<p>Volver a revisar los últimos <?= (int)$config->getResyncDias() ?> días.</p>
<p>Conjuntos de datos: <?= count($config->getVistas()) ?></p>
<p>Las conexiones ya están configuradas en el servidor. Las claves se mantienen ocultas.</p>
<p>Esta pantalla de pruebas no cambia las conexiones ni los horarios. Para traer datos nuevos ahora, volvé al panel y usá <strong>Sync</strong> o <strong>Sincronizar todo</strong>.</p>
</body></html>
