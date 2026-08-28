<?php
/**
 * worker.php
 * Worker de background para la cola de sincronizacion.
 * Ejecutado por Windows Task Scheduler cada minuto.
 *
 * Comando para Task Scheduler:
 *   Programa:   C:\wamp\bin\php\php5.5.12\php.exe
 *   Argumentos: C:\wamp\www\bi\worker.php
 *   Directorio: C:\wamp\www\bi
 *
 * Compatible con PHP 5.5+
 */

// Solo CLI — bloquear acceso web
if (php_sapi_name() !== 'cli') {
    http_response_code(403);
    die("Acceso denegado. worker.php solo puede ejecutarse desde CLI.\n");
}

define('BI_ROOT',      __DIR__);
define('LOCK_FILE',    BI_ROOT . '/logs/worker.lock');
define('WORKER_START', microtime(true));

// Sin limites — el worker puede correr horas
set_time_limit(0);
ini_set('memory_limit', '256M');

// Continuar aunque el "cliente" (Task Scheduler) cierre la conexion
ignore_user_abort(true);

// Alinear socket timeout con el timeout HTTP configurado
// Evita que PHP cierre el socket antes que CURL
// Se sobreescribe despues de cargar Config, pero este valor protege el bootstrap
ini_set('default_socket_timeout', 120);

require_once BI_ROOT . '/src/Config.php';
require_once BI_ROOT . '/src/Logger.php';
require_once BI_ROOT . '/src/Database.php';
require_once BI_ROOT . '/src/HttpClient.php';
require_once BI_ROOT . '/src/SchemaManager.php';
require_once BI_ROOT . '/src/DataLoader.php';
require_once BI_ROOT . '/src/SyncControl.php';
require_once BI_ROOT . '/src/ETLRunner.php';

/**
 * Detecta errores de acceso al backend (token desactivado, credenciales,
 * licencia, 401/403).
 *
 * El backend de Ekaru desactiva el token ante UN SOLO intento fallido y hay
 * que reactivarlo a mano desde el ERP (gestion > BI). Con el token muerto,
 * seguir pidiendo las 19 vistas restantes no recupera nada: solo llena el log
 * de errores en cascada y agrega intentos fallidos del lado del proveedor.
 *
 * @param  string $msg  Mensaje de la excepcion
 * @return bool
 */
function esErrorDeAcceso($msg)
{
    $patrones = array(
        'credenciales',
        'bloqueado',
        'API Key invalida',
        'apikey',
        'token',
        'licencia',
        'sin acceso',
        'HTTP 401',
        'HTTP 403',
    );

    foreach ($patrones as $p) {
        if (stripos($msg, $p) !== false) {
            return true;
        }
    }

    return false;
}

// ── Bootstrap ────────────────────────────────────────────────────────────────
$configPath = BI_ROOT . '/config.txt';

try {
    $config = new Config($configPath);
    $logger = new Logger(BI_ROOT . '/logs');
} catch (Exception $e) {
    $errMsg = date('[Y-m-d H:i:s]') . " [ERROR] Config: " . $e->getMessage() . "\n";
    @file_put_contents(BI_ROOT . '/logs/worker_bootstrap_error.log', $errMsg, FILE_APPEND);
    exit(1);
}

// Alinear socket timeout con config real
ini_set('default_socket_timeout', $config->getHttpTimeout() + 10);

$workerTimeout = $config->getWorkerTimeout();

// ── Lock: evitar dos workers simultaneos ──────────────────────────────────────
if (!is_dir(BI_ROOT . '/logs')) {
    mkdir(BI_ROOT . '/logs', 0755, true);
}

if (file_exists(LOCK_FILE)) {
    $lockAge = time() - filemtime(LOCK_FILE);

    if ($lockAge < ($workerTimeout * 60)) {
        // Worker ya corriendo y dentro del timeout — salir silenciosamente
        exit(0);
    }

    // Lock huerfano — el proceso anterior murio sin limpiar
    $logger->warning(
        "Lock huerfano detectado (" . round($lockAge / 60) . " min) — limpiando y continuando."
    );
    unlink(LOCK_FILE);
}

// Crear lock con PID
file_put_contents(LOCK_FILE, getmypid() . "\n" . date('Y-m-d H:i:s'));

// ── Conectar a DB ─────────────────────────────────────────────────────────────
try {
    $pdo         = Database::connect($config->getDbConfig());
    $syncControl = new SyncControl($pdo, $logger);
} catch (Exception $e) {
    $logger->error("Worker: fallo conexion MySQL: " . $e->getMessage());
    @unlink(LOCK_FILE);
    exit(1);
}

// Registrar que el worker esta vivo
$syncControl->workerHeartbeat();
$logger->info("=== Worker iniciado (PID: " . getmypid() . ") ===");

// ── Auto-retomar jobs pausados por causa transitoria ──────────────────────────
// Timeout de consulta, error de red u horario de carga del backend dejan el job
// en 'paused' conservando la staging. Tras un cooldown se re-encolan solos para
// retomar exactamente desde la pagina donde se cortaron — sin abrir la pagina ni
// clic manual. Las pausas manuales ('user'/'force_cancel') NO se tocan.
$autoResumeMin = (int)$config->get('auto_resume_minutes', 10);
if ($autoResumeMin > 0) {
    $resumed = $syncControl->autoResumeTransientPaused($autoResumeMin);
    if ($resumed > 0) {
        $logger->info(
            "Auto-retomados " . $resumed . " job(s) pausados por causa transitoria "
            . "(cooldown: " . $autoResumeMin . " min)."
        );
    }
}

// ── Auto-encolar vistas atrasadas ─────────────────────────────────────────────
// Si una vista tiene mas de auto_sync_hours sin sincronizar → encolar automaticamente
// Configurable en config.txt: auto_sync_hours = 24 (0 = desactivado)
$autoSyncHours = (int)$config->get('auto_sync_hours', 24);

if ($autoSyncHours > 0) {
    $vistas        = $config->getVistas();
    $allStates     = $syncControl->getAllStates();
    $statesByName  = array();

    foreach ($allStates as $s) {
        $statesByName[$s['vista_nombre']] = $s;
    }

    $autoEnqueued = 0;

    foreach ($vistas as $v) {
        $nombre = $v['nombre'];

        // Saltear si ya tiene un job activo (pending, running, paused)
        $activeJob = $syncControl->getActiveJob($nombre);
        if ($activeJob) continue;

        $state      = isset($statesByName[$nombre]) ? $statesByName[$nombre] : null;
        $needsSync  = false;
        $reason     = '';

        if ($state === null || $state['last_sync_status'] === 'never') {
            // Nunca sincronizada
            $needsSync = true;
            $reason    = 'nunca sincronizada';

        } elseif ($state['last_sync_status'] === 'ok' && !empty($state['last_sync_end'])) {
            // Verificar si paso mas de auto_sync_hours desde la ultima sync exitosa
            $lastSync = strtotime($state['last_sync_end']);
            $diffHours = (time() - $lastSync) / 3600;

            if ($diffHours >= $autoSyncHours) {
                $needsSync = true;
                $reason    = 'hace ' . round($diffHours, 1) . 'hs (limite: ' . $autoSyncHours . 'hs)';
            }

        } elseif ($state['last_sync_status'] === 'error') {
            // En error — re-intentar si paso al menos 1 hora
            $lastSync  = !empty($state['last_sync_end']) ? strtotime($state['last_sync_end']) : 0;
            $diffHours = $lastSync > 0 ? (time() - $lastSync) / 3600 : 999;

            if ($diffHours >= 1) {
                $needsSync = true;
                $reason    = 'estaba en error, reintentando';
            }
        }

        if ($needsSync) {
            $tablaLocal  = SchemaManager::sanitizeName($nombre);
            $endpointUrl = $config->getUrlBase() . '/' . ltrim($v['endpoint'], '/');
            $idpk        = SchemaManager::sanitizeIdpk(isset($v['idpk']) ? $v['idpk'] : '');

            $result = $syncControl->enqueue($nombre, $tablaLocal, $endpointUrl, $idpk);

            if ($result['queued']) {
                $logger->info("Auto-encolado: " . $nombre . " (" . $reason . ")");
                $autoEnqueued++;
            }
        }
    }

    if ($autoEnqueued > 0) {
        $logger->info("Auto-encoladas " . $autoEnqueued . " vistas atrasadas.");
    }
}
$processed         = 0;
$abortadoPorAcceso = false;
$abortadoPorRed    = false;

while (true) {
    // Renovar lock para que no sea detectado como huerfano
    touch(LOCK_FILE);

    // Reconectar si MySQL se cayo entre iteraciones
    if (!Database::ping()) {
        $logger->warning("MySQL desconectado — reconectando...");
        try {
            $pdo         = Database::reconnect();
            $syncControl = new SyncControl($pdo, $logger);
        } catch (Exception $e) {
            $logger->error("No se pudo reconectar a MySQL: " . $e->getMessage());
            break;
        }
    }

    // Obtener siguiente job de la cola
    $job = $syncControl->getNextPending($workerTimeout);

    if (!$job) {
        // Cola vacia — salir normalmente
        break;
    }

    $jobId    = (int)$job['id'];
    $vistaNom = $job['vista_nombre'];

    // Detectar si es retoma (tiene staging activa y pagina > 1)
    $isResume = (
        (int)$job['has_staging'] === 1 &&
        (int)$job['current_page'] > 1
    );

    $logger->info(
        "Job #" . $jobId . ": " . $vistaNom
        . ($isResume
            ? " (RETOMANDO desde pag." . $job['current_page']
              . ", last_staging_id=" . ($job['last_staging_id'] ?: 'N/A') . ")"
            : " (nueva sync)"
          )
    );

    // Buscar configuracion de la vista
    $vistas      = $config->getVistas();
    $vistaConfig = null;

    foreach ($vistas as $v) {
        if ($v['nombre'] === $vistaNom) {
            $vistaConfig = $v;
            break;
        }
    }

    if (!$vistaConfig) {
        $errMsg = "Vista '" . $vistaNom . "' no encontrada en config.txt";
        $syncControl->markJobError($jobId, $errMsg);
        $logger->error($errMsg);
        continue;
    }

    try {
        $runner = new ETLRunner($config, $logger);
        $result = $runner->syncVista($vistaConfig, $jobId, false, $isResume);

        if ($result['status'] === 'paused') {
            $logger->info(
                "Job #" . $jobId . " pausado en pagina " . $result['page'] . "."
            );

            // ── Corte en seco ante fallo de red ───────────────────────────
            // Si no hay conexion con el backend, las vistas restantes van a
            // fallar igual: son 3 reintentos por vista x 19 vistas de ruido.
            // Se corta la corrida; los jobs quedan pausados conservando su
            // staging y se auto-retoman solos cuando vuelva la conexion.
            // Las pausas manuales no traen 'reason', asi que no cortan.
            $motivo = isset($result['reason']) ? $result['reason'] : '';

            if ($motivo === 'network_error') {
                $logger->error(
                    "CORTE DE CORRIDA — sin conexion con el backend. "
                    . "No se procesan los jobs restantes. Se retoman solos "
                    . "cuando se restablezca la red."
                );
                $abortadoPorRed = true;
                break;
            }

        } else {
            $logger->info(
                "Job #" . $jobId . " completado: "
                . $result['records_processed'] . " registros en "
                . $result['elapsed'] . "s"
            );
            $processed++;
        }

    } catch (Exception $e) {
        $logger->error("Job #" . $jobId . " error fatal: " . $e->getMessage());

        // Asegurarse que el job quedo marcado como error y no como running
        // para que no trabe la cola
        try {
            $stmt = $pdo->prepare(
                "UPDATE bi_sync_queue
                 SET status = 'error',
                     error_message = ?,
                     has_staging = 0,
                     updated_at = NOW()
                 WHERE id = ? AND status = 'running'"
            );
            $stmt->execute(array(substr($e->getMessage(), 0, 2000), $jobId));
        } catch (Exception $ignored) {}

        // ── Corte en seco ante error de acceso ────────────────────────────
        // Si el token esta desactivado, las 19 vistas restantes van a fallar
        // igual. Se detiene la corrida completa: los jobs quedan 'pending' y
        // se retoman solos en la proxima corrida, una vez reactivado el token.
        if (esErrorDeAcceso($e->getMessage())) {
            $logger->error(
                "CORTE DE CORRIDA — error de acceso al backend. "
                . "No se procesan los jobs restantes para no generar mas "
                . "intentos fallidos. Reactivar el token en el ERP "
                . "(gestion > BI) y volver a correr."
            );
            $abortadoPorAcceso = true;
            break;
        }

        // Continuar con el siguiente job — no detener la cola
        $logger->info("Continuando con el siguiente job de la cola...");
    }

    // Heartbeat del worker tras cada job
    $syncControl->workerHeartbeat();
    touch(LOCK_FILE);
}

// ── Limpiar y salir ───────────────────────────────────────────────────────────
@unlink(LOCK_FILE);

$motivoCorte = '';
if ($abortadoPorAcceso) {
    $motivoCorte = " POR ERROR DE ACCESO";
} elseif ($abortadoPorRed) {
    $motivoCorte = " POR FALLO DE RED";
}

$elapsed = round(microtime(true) - WORKER_START, 2);
$logger->info(
    "=== Worker finalizado" . $motivoCorte . " — Jobs completados: "
    . $processed . " | Tiempo total: " . $elapsed . "s ==="
);

// Codigos de salida:
//   0 = corrida normal
//   2 = corte por error de acceso (token desactivado, credenciales)
//   3 = corte por fallo de red
// Permiten que una tarea programada o un script de alerta distinga cada caso.
if ($abortadoPorAcceso) {
    exit(2);
}
if ($abortadoPorRed) {
    exit(3);
}
exit(0);