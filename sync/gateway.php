<?php
require_once __DIR__ . '/web_auth.php';
header('Cache-Control: no-store, private');
header('Referrer-Policy: no-referrer');
header('X-Content-Type-Options: nosniff');
header('X-Frame-Options: SAMEORIGIN');
header("Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'");
if (($_SERVER['HTTPS'] ?? '') !== 'on') panel_fail('Ingresá al entorno de pruebas por HTTPS.');
$company = $_SERVER['KYBER_PANEL_COMPANY'] ?? '';
$page = $_SERVER['KYBER_PANEL_PAGE'] ?? '';
if (!in_array($company, array('ekaru', 'ejapo'), true)
    || !in_array($page, array('entrar', 'index.php', 'sync.php', 'configuracion.php'), true)) panel_fail('Página no disponible.', 404);
$state = getenv('KYBER_PANEL_STATE');
if (!$state || !is_dir($state)) panel_fail('El panel está en mantenimiento.', 503);
$panelBase = rtrim(panel_local_path('KYBER_PANEL_BASE_PATH', '/pruebas/panel-sync'), '/');
session_name('KYBER_PANEL_' . $company);
ini_set('session.use_strict_mode', '1');
session_save_path($state . '/sessions');
session_set_cookie_params(array('lifetime'=>0, 'path'=>$panelBase . '/' . $company . '/',
    'secure'=>true, 'httponly'=>true, 'samesite'=>'Strict'));
session_start();

if ($page === 'entrar') {
    if ($_SERVER['REQUEST_METHOD'] === 'GET') {
        // Fragment is removed before POST: no tickets in history/referrers/logs.
        header('Content-Type: text/html; charset=utf-8');
        echo '<!doctype html><html lang="es"><meta charset="utf-8"><title>Abrir panel</title>';
        echo '<body><p>Abriendo tu panel de sincronización…</p><script>';
        echo 'var t=location.hash.slice(1);history.replaceState(null,"",location.pathname);';
        echo 'if(!/^[a-f0-9]{64}$/.test(t)){location.replace(' . json_encode(panel_app_path()) . ')}else{';
        echo 'var f=document.createElement("form");f.method="POST";f.action=location.pathname;';
        echo 'var i=document.createElement("input");i.type="hidden";i.name="ticket";i.value=t;f.appendChild(i);document.body.appendChild(f);f.submit();}';
        echo '</script></body></html>'; exit;
    }
    if ($_SERVER['REQUEST_METHOD'] !== 'POST'
        || ($_SERVER['HTTP_ORIGIN'] ?? '') !== getenv('KYBER_PANEL_ORIGIN')) panel_fail('Volvé a abrir el panel desde Kyber.');
    $ticket = panel_consume_ticket($_POST['ticket'] ?? '', $company, $state);
    if (!$ticket || !panel_authorize_user($ticket['uid'], $company)) panel_fail('El acceso venció o tu usuario no tiene permiso. Volvé a Kyber y abrí el panel nuevamente.');
    session_regenerate_id(true);
    $_SESSION = array('access'=>$ticket, 'csrf'=>bin2hex(random_bytes(32)));
    header('Location: ' . $panelBase . '/' . $company . '/index.php', true, 303); exit;
}

$access = $_SESSION['access'] ?? null;
if (!panel_lease_valid($access, $state) || !panel_authorize_user($access['uid'], $company)) {
    $_SESSION = array(); session_destroy();
    panel_fail('Ingresá a Kyber como administrador y usá el botón «Panel de sincronización».');
}
define('KYBER_WEB_PANEL', true);
define('KYBER_PANEL_CSRF', $_SESSION['csrf']);
define('BI_ROOT', '/opt/kyber/sync/' . $company);
define('BI_CODE', __DIR__);

if ($page === 'sync.php') {
    $action = $_SERVER['REQUEST_METHOD'] === 'POST' ? ($_POST['action'] ?? '') : ($_GET['action'] ?? '');
    $read = array('status', 'history', 'log', 'prerequisites');
    $write = array('enqueue', 'pause', 'resume', 'restart', 'force_cancel', 'test_connection', 'update_vistas');
    if (!in_array($action, array_merge($read, $write), true)) panel_fail('Acción no disponible.', 400);
    if (in_array($action, $write, true)) {
        if (!panel_csrf_valid($_SERVER['REQUEST_METHOD'], $_SERVER['HTTP_X_KYBER_CSRF'] ?? '',
                KYBER_PANEL_CSRF, $_SERVER['HTTP_ORIGIN'] ?? '')) panel_fail('Solicitud no autorizada. Recargá el panel.');
        error_log('KYBER_PANEL ' . json_encode(array('uid'=>(int)$access['uid'], 'company'=>$company, 'action'=>$action)));
    }
    // Legacy API expects GET parameters, populated only after POST/CSRF checks.
    if ($_SERVER['REQUEST_METHOD'] === 'POST') $_GET = $_POST;
}
session_write_close();
ob_start('panel_redact');
try {
    if ($page === 'configuracion.php') require __DIR__ . '/web_config.php';
    else require __DIR__ . '/' . $page;
} catch (Throwable $e) {
    panel_fail('No pudimos consultar el sincronizador. Avisale a Adrián para revisar la conexión.', 503);
}
