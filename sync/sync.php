<?php
/**
 * sync.php
 * API AJAX para el panel de control.
 * Compatible con PHP 5.5+
 */

define('BI_ROOT', __DIR__);

set_time_limit(300);
ini_set('memory_limit', '128M');
ignore_user_abort(true);

require_once BI_ROOT . '/src/Config.php';
require_once BI_ROOT . '/src/Logger.php';
require_once BI_ROOT . '/src/Database.php';
require_once BI_ROOT . '/src/HttpClient.php';
require_once BI_ROOT . '/src/SchemaManager.php';
require_once BI_ROOT . '/src/DataLoader.php';
require_once BI_ROOT . '/src/SyncControl.php';
require_once BI_ROOT . '/src/ETLRunner.php';
require_once BI_ROOT . '/src/Prerequisites.php';

header('Cache-Control: no-cache, no-store');
header('X-Accel-Buffering: no');

$configPath = BI_ROOT . '/config.txt';
$action     = isset($_GET['action']) ? $_GET['action'] : '';
$vistaNom   = isset($_GET['vista'])  ? trim($_GET['vista']) : '';

// ── Cargar config ─────────────────────────────────────────────────────────────
try {
    $config = new Config($configPath);
    $logger = new Logger(BI_ROOT . '/logs');
} catch (Exception $e) {
    jsonResponse(array('ok' => false, 'error' => $e->getMessage()), 500);
    exit;
}

try {
    $pdo         = Database::connect($config->getDbConfig());
    $syncControl = new SyncControl($pdo, $logger);
} catch (Exception $e) {
    jsonResponse(array('ok' => false, 'error' => 'MySQL: ' . $e->getMessage()), 500);
    exit;
}

// ── Router ────────────────────────────────────────────────────────────────────
switch ($action) {

    // ── Estado completo del panel ─────────────────────────────────────────────
    case 'status':
        $vistas        = $config->getVistas();
        $states        = $syncControl->getAllStates();
        $queue         = $syncControl->getQueueStatus();
        $workerLastRun = $syncControl->getWorkerLastRun();

        $statesByName = array();
        foreach ($states as $s) {
            $statesByName[$s['vista_nombre']] = $s;
        }

        $queueByName = array();
        foreach ($queue as $q) {
            $queueByName[$q['vista_nombre']] = $q;
        }

        $result = array();
        foreach ($vistas as $v) {
            $nom   = $v['nombre'];
            $state = isset($statesByName[$nom]) ? $statesByName[$nom] : array();
            $qJob  = isset($queueByName[$nom])  ? $queueByName[$nom]  : null;

            $result[] = array(
                'nombre'                => $nom,
                'endpoint'              => isset($v['endpoint']) ? $v['endpoint'] : '',
                'idpk'                  => isset($v['idpk'])     ? $v['idpk']     : '',
                'last_sync_status'      => isset($state['last_sync_status'])      ? $state['last_sync_status']      : 'never',
                'last_sync_end'         => isset($state['last_sync_end'])         ? $state['last_sync_end']         : null,
                'total_records_local'   => isset($state['total_records_local'])   ? (int)$state['total_records_local']   : 0,
                'last_records_inserted' => isset($state['last_records_inserted']) ? (int)$state['last_records_inserted'] : 0,
                'last_records_updated'  => isset($state['last_records_updated'])  ? (int)$state['last_records_updated']  : 0,
                'last_error_message'    => isset($state['last_error_message'])    ? $state['last_error_message']    : null,
                'sync_count'            => isset($state['sync_count'])            ? (int)$state['sync_count']       : 0,
                'queue_job'             => $qJob,
            );
        }

        $workerStatus = getWorkerStatus($workerLastRun, BI_ROOT . '/logs/worker.lock');

        jsonResponse(array(
            'ok'          => true,
            'vistas'      => $result,
            'worker'      => $workerStatus,
            'server_time' => date('Y-m-d H:i:s'),
        ));
        break;

    // ── Encolar una vista ─────────────────────────────────────────────────────
    case 'enqueue':
        if (empty($vistaNom)) {
            jsonResponse(array('ok' => false, 'error' => 'Parametro vista requerido'), 400);
            break;
        }

        $vistaConfig = findVista($config->getVistas(), $vistaNom);
        if (!$vistaConfig) {
            jsonResponse(array('ok' => false, 'error' => "Vista '{$vistaNom}' no encontrada"), 404);
            break;
        }

        $tablaLocal  = SchemaManager::sanitizeName($vistaNom);
        $endpointUrl = $config->getUrlBase() . '/' . ltrim($vistaConfig['endpoint'], '/');
        $idpk        = SchemaManager::sanitizeIdpk(isset($vistaConfig['idpk']) ? $vistaConfig['idpk'] : '');

        $result = $syncControl->enqueue($vistaNom, $tablaLocal, $endpointUrl, $idpk);
        jsonResponse(array('ok' => true, 'result' => $result));
        break;

    // ── Pausar una vista ──────────────────────────────────────────────────────
    case 'pause':
        if (empty($vistaNom)) {
            jsonResponse(array('ok' => false, 'error' => 'Parametro vista requerido'), 400);
            break;
        }

        $job = $syncControl->getActiveJob($vistaNom);
        if (!$job) {
            jsonResponse(array('ok' => false, 'error' => 'No hay job activo para pausar'), 404);
            break;
        }

        file_put_contents(BI_ROOT . '/logs/pause_' . md5($vistaNom) . '.flag', '1');
        jsonResponse(array('ok' => true, 'message' => 'Pausa solicitada'));
        break;

    // ── Retomar vista pausada ─────────────────────────────────────────────────
    case 'resume':
        if (empty($vistaNom)) {
            jsonResponse(array('ok' => false, 'error' => 'Parametro vista requerido'), 400);
            break;
        }

        $job = $syncControl->getPausedJob($vistaNom);
        if (!$job) {
            jsonResponse(array('ok' => false, 'error' => "No hay job pausado para '{$vistaNom}'"), 404);
            break;
        }

        $syncControl->resumeJob($job['id']);
        @unlink(BI_ROOT . '/logs/pause_' . md5($vistaNom) . '.flag');
        jsonResponse(array('ok' => true, 'message' => 'Job retomado, sera procesado por el worker'));
        break;

    // ── Reiniciar vista (descarta staging, desde cero) ────────────────────────
    case 'restart':
        if (empty($vistaNom)) {
            jsonResponse(array('ok' => false, 'error' => 'Parametro vista requerido'), 400);
            break;
        }

        $syncControl->restartJob($vistaNom);

        $tablaLocal    = SchemaManager::sanitizeName($vistaNom);
        $schemaManager = new SchemaManager($pdo, $logger);
        $schemaManager->dropStagingTable($tablaLocal);

        @unlink(BI_ROOT . '/logs/pause_' . md5($vistaNom) . '.flag');

        $vistaConfig = findVista($config->getVistas(), $vistaNom);
        if ($vistaConfig) {
            $endpointUrl = $config->getUrlBase() . '/' . ltrim($vistaConfig['endpoint'], '/');
            $idpk        = SchemaManager::sanitizeIdpk(isset($vistaConfig['idpk']) ? $vistaConfig['idpk'] : '');
            $syncControl->enqueue($vistaNom, $tablaLocal, $endpointUrl, $idpk);
        }

        jsonResponse(array('ok' => true, 'message' => 'Vista reiniciada desde cero'));
        break;

    // ── Forzar cancelacion de job colgado ─────────────────────────────────────
    case 'force_cancel':
        if (empty($vistaNom)) {
            jsonResponse(array('ok' => false, 'error' => 'Parametro vista requerido'), 400);
            break;
        }

        $job = $syncControl->getActiveJob($vistaNom);
        if (!$job) {
            jsonResponse(array('ok' => false, 'error' => "No hay job activo para '{$vistaNom}'"), 404);
            break;
        }

        $tablaLocal    = SchemaManager::sanitizeName($vistaNom);
        $schemaManager = new SchemaManager($pdo, $logger);
        $hasStaging    = (int)$job['has_staging'] === 1;

        if ($hasStaging) {
            $stmt = $pdo->prepare(
                "UPDATE bi_sync_queue
                 SET status = 'paused', pause_reason = 'force_cancel', updated_at = NOW()
                 WHERE id = ?"
            );
            $stmt->execute(array($job['id']));
            $msg = 'Job pausado — tiene progreso guardado, puede retomar cuando quiera.';
        } else {
            $stmt = $pdo->prepare(
                "UPDATE bi_sync_queue
                 SET status = 'error',
                     error_message = 'Cancelado manualmente por el usuario',
                     updated_at = NOW()
                 WHERE id = ?"
            );
            $stmt->execute(array($job['id']));
            $schemaManager->dropStagingTable($tablaLocal);
            $msg = 'Job cancelado — sin progreso guardado, debe reiniciar la sincronizacion.';
        }

        $stmt = $pdo->prepare(
            "UPDATE bi_sync_control
             SET last_sync_status   = 'error',
                 last_error_message = 'Cancelado manualmente',
                 updated_at         = NOW()
             WHERE vista_nombre = ? AND last_sync_status = 'running'"
        );
        $stmt->execute(array($vistaNom));

        $lockFile = BI_ROOT . '/logs/worker.lock';
        if (file_exists($lockFile) && (time() - filemtime($lockFile)) > 600) {
            @unlink($lockFile);
            $msg .= ' Lock file limpiado.';
        }

        $logger->info("Force cancel: " . $vistaNom . " — " . $msg);
        jsonResponse(array('ok' => true, 'message' => $msg, 'had_staging' => $hasStaging));
        break;

    // ── Historial de una vista ────────────────────────────────────────────────
    case 'history':
        if (empty($vistaNom)) {
            jsonResponse(array('ok' => false, 'error' => 'Parametro vista requerido'), 400);
            break;
        }
        $history = $syncControl->getHistory($vistaNom, 20);
        jsonResponse(array('ok' => true, 'history' => $history));
        break;

    // ── Leer log ──────────────────────────────────────────────────────────────
    case 'log':
        $file     = isset($_GET['file']) ? $_GET['file'] : '';
        $safeFile = basename($file);
        $fullPath = BI_ROOT . '/logs/' . $safeFile;

        if (empty($safeFile) || !preg_match('/\.log$/', $safeFile) || !file_exists($fullPath)) {
            jsonResponse(array('ok' => false, 'error' => 'Log no encontrado'), 404);
            break;
        }

        $content = $logger->readLog($fullPath, 300);
        jsonResponse(array('ok' => true, 'content' => $content));
        break;

    // ── Test de conexion ──────────────────────────────────────────────────────
    case 'test_connection':
        if (empty($vistaNom)) {
            jsonResponse(array('ok' => false, 'error' => 'Parametro vista requerido'), 400);
            break;
        }

        $vistaConfig = findVista($config->getVistas(), $vistaNom);
        if (!$vistaConfig) {
            jsonResponse(array('ok' => false, 'error' => "Vista no encontrada"), 404);
            break;
        }

        $endpointUrl = $config->getUrlBase() . '/' . ltrim($vistaConfig['endpoint'], '/');
        $http        = new HttpClient($config->getApiKey(), $logger, 15, 1, 0);
        $result      = $http->testConnection($endpointUrl);

        jsonResponse(array(
            'ok'      => $result['ok'],
            'error'   => $result['error'],
            'schema'  => isset($result['data']['schema']) ? $result['data']['schema'] : null,
            'records' => isset($result['data']['data'])   ? count($result['data']['data']) : 0,
        ));
        break;

    // ── Actualizar vistas desde el backend ────────────────────────────────────
    case 'update_vistas':
        $wsUrl  = $config->getVistasWsUrl();
        $result = descargarVistas($wsUrl, $config->getHttpTimeout());

        if (!$result['ok']) {
            jsonResponse(array('ok' => false, 'error' => $result['error']));
            break;
        }

        $vistasPath    = BI_ROOT . '/vistas.txt';
        $vistasContent = "; Ekaru BI Sync — Vistas\n"
            . "; Actualizado automaticamente: " . date('Y-m-d H:i:s') . "\n"
            . "; NO editar manualmente\n\n"
            . "[vistas]\n"
            . "json = " . json_encode($result['vistas'], JSON_UNESCAPED_UNICODE) . "\n";

        if (file_put_contents($vistasPath, $vistasContent) === false) {
            jsonResponse(array(
                'ok'    => false,
                'error' => "No se pudo escribir vistas.txt — verificar permisos en " . BI_ROOT,
            ));
            break;
        }

        $logger->info("Vistas actualizadas: " . count($result['vistas']) . " vistas.");
        jsonResponse(array(
            'ok'      => true,
            'count'   => count($result['vistas']),
            'updated' => date('Y-m-d H:i:s'),
            'message' => count($result['vistas']) . " vistas actualizadas correctamente.",
        ));
        break;

    // ── Chequeo de prerequisitos ──────────────────────────────────────────────
    case 'prerequisites':
        $prereq  = new Prerequisites(BI_ROOT);
        $results = $prereq->check();
        $env     = $prereq->getEnvironment();

        jsonResponse(array(
            'ok'     => $prereq->allOk(),
            'checks' => array_values($results),
            'env'    => $env,
        ));
        break;

    // ── Sin accion ────────────────────────────────────────────────────────────
    default:
        jsonResponse(array('ok' => false, 'error' => 'Accion no reconocida: ' . $action), 400);
        break;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function jsonResponse($data, $code = 200)
{
    http_response_code($code);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($data);
    exit;
}

function findVista($vistas, $nombre)
{
    foreach ($vistas as $v) {
        if ($v['nombre'] === $nombre) return $v;
    }
    return null;
}

function getWorkerStatus($lastRun, $lockFile)
{
    $lockExists = file_exists($lockFile);
    $lockAge    = $lockExists ? (time() - filemtime($lockFile)) : null;
    $isRunning  = $lockExists && $lockAge !== null && $lockAge < 120;

    if ($lastRun === null) {
        return array(
            'status'   => 'never',
            'message'  => 'Worker nunca ejecutado — configurar Task Scheduler',
            'last_run' => null,
            'running'  => false,
        );
    }

    $lastRunTs = strtotime($lastRun);
    $diffMin   = round((time() - $lastRunTs) / 60);

    if ($isRunning) {
        return array(
            'status'   => 'running',
            'message'  => 'Worker activo ahora',
            'last_run' => $lastRun,
            'running'  => true,
        );
    }

    if ($diffMin <= 2) {
        return array(
            'status'   => 'ok',
            'message'  => 'Worker activo — ultima ejecucion hace ' . $diffMin . ' min',
            'last_run' => $lastRun,
            'running'  => false,
        );
    }

    if ($diffMin <= 10) {
        return array(
            'status'   => 'ok',
            'message'  => 'Worker OK — hace ' . $diffMin . ' min',
            'last_run' => $lastRun,
            'running'  => false,
        );
    }

    if ($diffMin <= 60) {
        return array(
            'status'   => 'warning',
            'message'  => 'Worker sin actividad hace ' . $diffMin . ' min',
            'last_run' => $lastRun,
            'running'  => false,
        );
    }

    return array(
        'status'   => 'error',
        'message'  => 'Worker inactivo hace ' . round($diffMin / 60, 1) . ' horas — verificar Task Scheduler',
        'last_run' => $lastRun,
        'running'  => false,
    );
}

function descargarVistas($wsUrl, $timeout = 30)
{
    $ch = curl_init();
    curl_setopt_array($ch, array(
        CURLOPT_URL            => $wsUrl,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT        => $timeout,
        CURLOPT_CONNECTTIMEOUT => 10,
        CURLOPT_ENCODING       => 'gzip, deflate',
        CURLOPT_USERAGENT      => 'EkaruBI-ETL/1.0',
        CURLOPT_SSL_VERIFYPEER => false,
        CURLOPT_SSL_VERIFYHOST => false,
    ));

    $body     = curl_exec($ch);
    $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $curlErr  = curl_errno($ch);
    $curlMsg  = curl_error($ch);
    curl_close($ch);

    if ($body === false || $curlErr !== 0) {
        return array('ok' => false, 'vistas' => array(),
            'error' => "Error de conexion al backend: " . $curlMsg);
    }

    if ($httpCode === 401 || $httpCode === 403) {
        return array('ok' => false, 'vistas' => array(),
            'error' => "HTTP " . $httpCode . " — API Key invalida");
    }

    if ($httpCode < 200 || $httpCode >= 300) {
        return array('ok' => false, 'vistas' => array(),
            'error' => "HTTP " . $httpCode . " — Error del servidor");
    }

    $json = json_decode($body, true);

    if (json_last_error() !== JSON_ERROR_NONE || !is_array($json)) {
        return array('ok' => false, 'vistas' => array(),
            'error' => "Respuesta invalida del servidor: " . json_last_error_msg());
    }

    if (empty($json)) {
        return array('ok' => false, 'vistas' => array(),
            'error' => "El servidor devolvio una lista de vistas vacia");
    }

    $vistas = array();
    foreach ($json as $v) {
        if (!isset($v['nombre']) || !isset($v['endpoint'])) continue;
        $vistas[] = array(
            'nombre'   => $v['nombre'],
            'endpoint' => $v['endpoint'],
            'idpk'     => isset($v['idpk']) ? $v['idpk'] : '',
            'keyset'   => isset($v['keyset']) ? $v['keyset'] : false,
        );
    }

    return array('ok' => true, 'vistas' => $vistas, 'error' => '');
}
