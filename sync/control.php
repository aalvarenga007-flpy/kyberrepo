<?php
/**
 * Control CLI del sincronizador para el panel autenticado de Kyber.
 *
 * Nunca se sirve por HTTP. No imprime endpoints, claves ni contrasenas.
 * Compatible con PHP 7.4 (Rocky Linux).
 */

if (PHP_SAPI !== 'cli') {
    http_response_code(404);
    exit;
}

define('BI_ROOT', __DIR__);

require_once BI_ROOT . '/src/Config.php';
require_once BI_ROOT . '/src/Logger.php';
require_once BI_ROOT . '/src/Database.php';
require_once BI_ROOT . '/src/SchemaManager.php';
require_once BI_ROOT . '/src/SyncControl.php';

function respond($payload, $code = 0)
{
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . PHP_EOL;
    exit($code);
}

function findView($views, $name)
{
    foreach ($views as $view) {
        if (isset($view['nombre']) && hash_equals((string)$view['nombre'], (string)$name)) {
            return $view;
        }
    }
    return null;
}

function safeQueue($job)
{
    if (!$job) return null;
    return array(
        'id'                 => (int)$job['id'],
        'status'             => (string)$job['status'],
        'current_page'       => (int)$job['current_page'],
        'records_downloaded' => (int)$job['records_downloaded'],
        'worker_heartbeat'   => $job['worker_heartbeat'],
        'created_at'         => $job['created_at'],
    );
}

try {
    $config = new Config(BI_ROOT . '/config.txt');
    $logger = new Logger(BI_ROOT . '/logs');
    $pdo = Database::connect($config->getDbConfig());
    $control = new SyncControl($pdo, $logger);
    $views = $config->getVistas();
    $action = isset($argv[1]) ? (string)$argv[1] : 'status';

    if ($action === 'status') {
        $statesByName = array();
        foreach ($control->getAllStates() as $state) {
            $statesByName[$state['vista_nombre']] = $state;
        }
        $queueByName = array();
        foreach ($control->getQueueStatus() as $job) {
            $queueByName[$job['vista_nombre']] = $job;
        }

        $rows = array();
        foreach ($views as $view) {
            $name = (string)$view['nombre'];
            $state = isset($statesByName[$name]) ? $statesByName[$name] : array();
            $rows[] = array(
                'name'                  => $name,
                'last_sync_start'       => isset($state['last_sync_start']) ? $state['last_sync_start'] : null,
                'last_sync_end'         => isset($state['last_sync_end']) ? $state['last_sync_end'] : null,
                'last_sync_status'      => isset($state['last_sync_status']) ? $state['last_sync_status'] : 'never',
                'total_records_local'   => isset($state['total_records_local']) ? (int)$state['total_records_local'] : 0,
                'last_records_inserted' => isset($state['last_records_inserted']) ? (int)$state['last_records_inserted'] : 0,
                'last_records_updated'  => isset($state['last_records_updated']) ? (int)$state['last_records_updated'] : 0,
                'sync_count'            => isset($state['sync_count']) ? (int)$state['sync_count'] : 0,
                'has_error'             => !empty($state['last_error_message']),
                'queue'                 => safeQueue(isset($queueByName[$name]) ? $queueByName[$name] : null),
            );
        }

        $history = array();
        foreach ($control->getHistory('Ventas', 10) as $item) {
            $history[] = array(
                'started_at'       => $item['started_at'],
                'finished_at'      => $item['finished_at'],
                'status'           => $item['status'],
                'pages_downloaded' => (int)$item['pages_downloaded'],
                'records_inserted' => (int)$item['records_inserted'],
                'records_updated'  => (int)$item['records_updated'],
                'elapsed_seconds'  => (float)$item['elapsed_seconds'],
                'has_error'        => !empty($item['error_message']),
            );
        }

        respond(array(
            'ok'                => true,
            'server_time'       => date('Y-m-d H:i:s'),
            'worker_last_run'   => $control->getWorkerLastRun(),
            'auto_sync_hours'   => (int)$config->get('auto_sync_hours', 24),
            'resync_days'       => (int)$config->get('resync_dias', 0),
            'views'             => $rows,
            'sales_history'     => $history,
        ));
    }

    if ($action !== 'enqueue' && $action !== 'enqueue-all') {
        respond(array('ok' => false, 'error' => 'Accion no valida.'), 2);
    }

    $selected = $views;
    if ($action === 'enqueue') {
        $name = isset($argv[2]) ? trim((string)$argv[2]) : '';
        $view = findView($views, $name);
        if (!$view) respond(array('ok' => false, 'error' => 'Vista no valida.'), 2);
        $selected = array($view);
    }

    $queued = 0;
    $alreadyActive = 0;
    foreach ($selected as $view) {
        $name = (string)$view['nombre'];
        $localTable = SchemaManager::sanitizeName($name);
        $endpoint = $config->getUrlBase() . '/' . ltrim((string)$view['endpoint'], '/');
        $idpk = SchemaManager::sanitizeIdpk(isset($view['idpk']) ? $view['idpk'] : '');
        $control->upsertState($name, $localTable, $endpoint, $idpk);
        $result = $control->enqueue($name, $localTable, $endpoint, $idpk, 100);
        if (!empty($result['queued'])) $queued++;
        else $alreadyActive++;
    }

    respond(array(
        'ok'             => true,
        'queued'         => $queued,
        'already_active' => $alreadyActive,
        'requested'      => count($selected),
    ));
} catch (Exception $e) {
    respond(array('ok' => false, 'error' => 'No se pudo operar el sincronizador.'), 1);
}
