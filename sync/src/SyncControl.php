<?php
/**
 * SyncControl.php
 * Tabla de control de sincronizacion y cola de trabajos.
 *
 * Cambios v2:
 * - cleanStaleJobs: pausa (no error) si tiene staging activa
 * - updateHeartbeat: guarda last_staging_id (MAX idpk en staging)
 * - columna last_staging_id en bi_sync_queue
 *
 * Compatible con PHP 5.5+ / MySQL 5.6+
 */

class SyncControl
{
    private $pdo;
    private $logger;

    const TABLE_CONTROL = 'bi_sync_control';
    const TABLE_QUEUE   = 'bi_sync_queue';
    const TABLE_HISTORY = 'bi_sync_history';

    const STATUS_PENDING  = 'pending';
    const STATUS_RUNNING  = 'running';
    const STATUS_PAUSED   = 'paused';
    const STATUS_DONE     = 'done';
    const STATUS_ERROR    = 'error';

    public function __construct($pdo, $logger, $ensureTables = true)
    {
        $this->pdo    = $pdo;
        $this->logger = $logger;
        if ($ensureTables) $this->ensureTables();
    }

    // ── Creacion de tablas ────────────────────────────────────────────────────

    private function ensureTables()
    {
        // Tabla de control por vista
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS `" . self::TABLE_CONTROL . "` (
            `id`                    INT UNSIGNED  NOT NULL AUTO_INCREMENT,
            `vista_nombre`          VARCHAR(100)  NOT NULL,
            `tabla_local`           VARCHAR(100)  NOT NULL DEFAULT '',
            `endpoint_url`          VARCHAR(500)  NOT NULL DEFAULT '',
            `idpk_param`            VARCHAR(200)  NOT NULL DEFAULT '',
            `last_id`               BIGINT        NULL DEFAULT NULL,
            `last_sync_start`       DATETIME      NULL DEFAULT NULL,
            `last_sync_end`         DATETIME      NULL DEFAULT NULL,
            `last_sync_status`      VARCHAR(20)   NOT NULL DEFAULT 'never',
            `last_error_message`    TEXT          NULL DEFAULT NULL,
            `total_records_local`   BIGINT        NOT NULL DEFAULT 0,
            `last_records_inserted` INT           NOT NULL DEFAULT 0,
            `last_records_updated`  INT           NOT NULL DEFAULT 0,
            `sync_count`            INT UNSIGNED  NOT NULL DEFAULT 0,
            `created_at`            DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at`            DATETIME      NULL DEFAULT NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_vista` (`vista_nombre`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8");

        // Cola de trabajos
        // last_staging_id: MAX(idpk) en staging al ultimo heartbeat
        // Permite retomar desde el registro exacto en caso de corte
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS `" . self::TABLE_QUEUE . "` (
            `id`                 INT UNSIGNED  NOT NULL AUTO_INCREMENT,
            `vista_nombre`       VARCHAR(100)  NOT NULL,
            `tabla_local`        VARCHAR(100)  NOT NULL DEFAULT '',
            `endpoint_url`       VARCHAR(500)  NOT NULL DEFAULT '',
            `idpk_param`         VARCHAR(200)  NOT NULL DEFAULT '',
            `status`             VARCHAR(20)   NOT NULL DEFAULT 'pending',
            `priority`           TINYINT       NOT NULL DEFAULT 0,
            `current_page`       INT           NOT NULL DEFAULT 1,
            `records_downloaded` INT           NOT NULL DEFAULT 0,
            `last_staging_id`    BIGINT        NULL DEFAULT NULL,
            `has_staging`        TINYINT(1)    NOT NULL DEFAULT 0,
            `error_message`      TEXT          NULL DEFAULT NULL,
            `pause_reason`       VARCHAR(100)  NULL DEFAULT NULL,
            `worker_started_at`  DATETIME      NULL DEFAULT NULL,
            `worker_heartbeat`   DATETIME      NULL DEFAULT NULL,
            `created_at`         DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at`         DATETIME      NULL DEFAULT NULL,
            PRIMARY KEY (`id`),
            INDEX `idx_status` (`status`),
            INDEX `idx_vista`  (`vista_nombre`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8");

        // Historial
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS `" . self::TABLE_HISTORY . "` (
            `id`               INT UNSIGNED NOT NULL AUTO_INCREMENT,
            `vista_nombre`     VARCHAR(100) NOT NULL,
            `started_at`       DATETIME     NOT NULL,
            `finished_at`      DATETIME     NULL DEFAULT NULL,
            `status`           VARCHAR(20)  NOT NULL DEFAULT 'ok',
            `pages_downloaded` INT          NOT NULL DEFAULT 0,
            `records_inserted` INT          NOT NULL DEFAULT 0,
            `records_updated`  INT          NOT NULL DEFAULT 0,
            `elapsed_seconds`  DOUBLE       NOT NULL DEFAULT 0,
            `error_message`    TEXT         NULL DEFAULT NULL,
            PRIMARY KEY (`id`),
            INDEX `idx_vista`   (`vista_nombre`),
            INDEX `idx_started` (`started_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8");

        // Migrar tabla si ya existe sin columna last_staging_id
        $this->addColumnIfMissing(
            self::TABLE_QUEUE,
            'last_staging_id',
            'BIGINT NULL DEFAULT NULL',
            'records_downloaded'
        );

        $this->addColumnIfMissing(
            self::TABLE_QUEUE,
            'pause_reason',
            "VARCHAR(100) NULL DEFAULT NULL",
            'has_staging'
        );
    }

    /**
     * Agrega una columna si no existe (migracion segura)
     */
    private function addColumnIfMissing($table, $column, $definition, $after = null)
    {
        $stmt = $this->pdo->prepare(
            "SELECT COUNT(*) FROM information_schema.columns
             WHERE table_schema = DATABASE()
               AND table_name   = ?
               AND column_name  = ?"
        );
        $stmt->execute(array($table, $column));

        if ((int)$stmt->fetchColumn() === 0) {
            $afterClause = $after ? " AFTER `{$after}`" : '';
            try {
                $this->pdo->exec(
                    "ALTER TABLE `{$table}` ADD COLUMN `{$column}` {$definition}{$afterClause}"
                );
            } catch (PDOException $e) {
                // Ignorar si falla (puede ser race condition en instalacion)
            }
        }
    }

    // ── SyncControl: estado por vista ─────────────────────────────────────────

    public function getState($vistaNombre)
    {
        $stmt = $this->pdo->prepare(
            "SELECT * FROM `" . self::TABLE_CONTROL . "` WHERE vista_nombre = ? LIMIT 1"
        );
        $stmt->execute(array($vistaNombre));
        $row = $stmt->fetch(PDO::FETCH_ASSOC);
        return $row ? $row : null;
    }

    public function getAllStates()
    {
        $stmt = $this->pdo->query(
            "SELECT * FROM `" . self::TABLE_CONTROL . "` ORDER BY vista_nombre"
        );
        return $stmt->fetchAll(PDO::FETCH_ASSOC);
    }

    /**
     * Retorna el ultimo ID sincronizado.
     * Si el idpk cambio → retorna 1 (re-sync completo).
     */
    public function getLastId($vistaNombre, $currentIdpk)
    {
        $state = $this->getState($vistaNombre);

        if ($state === null || $state['last_id'] === null) {
            return 1;
        }

        if ($state['idpk_param'] !== $currentIdpk) {
            $this->logger->info(
                "idpk cambio de '" . $state['idpk_param'] . "'"
                . " a '" . $currentIdpk . "' — re-sincronizando desde cero."
            );
            return 1;
        }

        return (int)$state['last_id'];
    }

    public function upsertState($vistaNombre, $tablaLocal, $endpointUrl, $idpkParam)
    {
        $state = $this->getState($vistaNombre);

        if ($state === null) {
            $stmt = $this->pdo->prepare(
                "INSERT INTO `" . self::TABLE_CONTROL . "`
                 (vista_nombre, tabla_local, endpoint_url, idpk_param, last_sync_status, updated_at)
                 VALUES (?, ?, ?, ?, 'never', NOW())"
            );
            $stmt->execute(array($vistaNombre, $tablaLocal, $endpointUrl, $idpkParam));
        } else {
            $stmt = $this->pdo->prepare(
                "UPDATE `" . self::TABLE_CONTROL . "`
                 SET tabla_local = ?, endpoint_url = ?, idpk_param = ?, updated_at = NOW()
                 WHERE vista_nombre = ?"
            );
            $stmt->execute(array($tablaLocal, $endpointUrl, $idpkParam, $vistaNombre));
        }
    }

    public function markSyncStart($vistaNombre)
    {
        $stmt = $this->pdo->prepare(
            "UPDATE `" . self::TABLE_CONTROL . "`
             SET last_sync_start = NOW(), last_sync_status = 'running',
                 last_error_message = NULL, updated_at = NOW()
             WHERE vista_nombre = ?"
        );
        $stmt->execute(array($vistaNombre));
    }

    public function markSyncDone($vistaNombre, $lastId, $totalLocal, $inserted, $updated)
    {
        $stmt = $this->pdo->prepare(
            "UPDATE `" . self::TABLE_CONTROL . "`
             SET last_id               = ?,
                 last_sync_end         = NOW(),
                 last_sync_status      = 'ok',
                 last_error_message    = NULL,
                 total_records_local   = ?,
                 last_records_inserted = ?,
                 last_records_updated  = ?,
                 sync_count            = sync_count + 1,
                 updated_at            = NOW()
             WHERE vista_nombre = ?"
        );
        $stmt->execute(array($lastId, $totalLocal, $inserted, $updated, $vistaNombre));
    }

    public function markSyncError($vistaNombre, $errorMessage)
    {
        $stmt = $this->pdo->prepare(
            "UPDATE `" . self::TABLE_CONTROL . "`
             SET last_sync_end      = NOW(),
                 last_sync_status   = 'error',
                 last_error_message = ?,
                 updated_at         = NOW()
             WHERE vista_nombre = ?"
        );
        $stmt->execute(array(substr($errorMessage, 0, 2000), $vistaNombre));
    }

    public function countLocalRecords($tablaLocal)
    {
        try {
            $quoted = '`' . str_replace('`', '``', $tablaLocal) . '`';
            $stmt   = $this->pdo->query("SELECT COUNT(*) FROM " . $quoted);
            return (int)$stmt->fetchColumn();
        } catch (PDOException $e) {
            return 0;
        }
    }

    public function getMaxLocalId($tablaLocal, $pkColumn)
    {
        try {
            $quotedTable = '`' . str_replace('`', '``', $tablaLocal) . '`';
            $quotedCol   = '`' . str_replace('`', '``', $pkColumn)   . '`';
            $stmt        = $this->pdo->query(
                "SELECT MAX(" . $quotedCol . ") FROM " . $quotedTable
            );
            $max = $stmt->fetchColumn();
            return ($max !== null && $max !== false) ? (int)$max : 1;
        } catch (PDOException $e) {
            return 1;
        }
    }

    // ── Ventana movil ─────────────────────────────────────────────────────────

    /**
     * Columna de fecha de la tabla local, para calcular la ventana movil.
     *
     * Se detecta sola porque cada vista tiene la suya (Fecha_Hora en Ventas,
     * fecha en Compras, etc.) y no queremos una lista a mano que se
     * desactualice cada vez que el backend agrega una vista.
     *
     * Devuelve null si la tabla no tiene ninguna columna de fecha.
     */
    public function detectarColumnaFecha($tablaLocal)
    {
        try {
            $stmt = $this->pdo->prepare(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS
                  WHERE TABLE_SCHEMA = DATABASE()
                    AND TABLE_NAME   = ?
                    AND DATA_TYPE IN ('date','datetime','timestamp')
                  ORDER BY ORDINAL_POSITION"
            );
            $stmt->execute(array($tablaLocal));
            $cols = $stmt->fetchAll(PDO::FETCH_COLUMN);

            if (empty($cols)) return null;

            // Preferir la fecha del DOCUMENTO. Si hay varias, gana la primera
            // que coincida con estas pistas, en este orden.
            $pistas = array('fecha_hora', 'fecha_emis', 'fecha_comprob', 'fecha', 'emis', 'comprob', 'hora');
            foreach ($pistas as $p) {
                foreach ($cols as $c) {
                    if (stripos($c, $p) !== false) return $c;
                }
            }
            return $cols[0];

        } catch (PDOException $e) {
            return null;
        }
    }

    /**
     * Cursor de la ventana movil, expresado en IDs.
     *
     * Devuelve el ID inmediatamente anterior al primer registro de los
     * ultimos $dias dias. Arrancar el cursor ahi hace que la sincronizacion
     * vuelva a leer esa ventana completa y, via el upsert del merge,
     * actualice todo lo que haya cambiado adentro.
     *
     * Devuelve null si no se pudo calcular (no hay columna de fecha, la tabla
     * esta vacia, o la ventana esta desactivada). En ese caso el llamador deja
     * el cursor como estaba: la ventana nunca puede EMPEORAR lo que ya hacia.
     */
    public function getCursorVentana($tablaLocal, $pkColumn, $dias, $margenIds = 0, $maxLocal = null)
    {
        if ($dias <= 0 && $margenIds <= 0) return null;
        if (empty($pkColumn)) return null;

        if ($dias > 0) {
            $col = $this->detectarColumnaFecha($tablaLocal);
            if ($col !== null) {
                try {
                    $qt = '`' . str_replace('`', '``', $tablaLocal) . '`';
                    $qp = '`' . str_replace('`', '``', $pkColumn)   . '`';
                    $qc = '`' . str_replace('`', '``', $col)        . '`';

                    $stmt = $this->pdo->prepare(
                        "SELECT MIN(" . $qp . ") FROM " . $qt
                        . " WHERE " . $qc . " >= DATE_SUB(CURDATE(), INTERVAL ? DAY)"
                    );
                    $stmt->execute(array((int)$dias));
                    $min = $stmt->fetchColumn();

                    if ($min !== null && $min !== false) {
                        $this->logger->info(
                            "Ventana movil: " . $dias . " dias por " . $tablaLocal . "." . $col
                            . " -> primer ID de la ventana = " . (int)$min
                        );
                        return max(0, (int)$min - 1);
                    }

                    // Tabla sin registros en la ventana (negocio cerrado, vista
                    // nueva). No es un error: se cae al margen fijo si lo hay.
                    $this->logger->info(
                        "Ventana movil: " . $tablaLocal . " no tiene registros en los ultimos "
                        . $dias . " dias."
                    );

                } catch (PDOException $e) {
                    $this->logger->warning(
                        "Ventana movil: no se pudo calcular sobre " . $tablaLocal
                        . " (" . $e->getMessage() . ")"
                    );
                }
            } else {
                $this->logger->info(
                    "Ventana movil: " . $tablaLocal . " no tiene columna de fecha; "
                    . "se usa el margen fijo de IDs si esta configurado."
                );
            }
        }

        if ($margenIds > 0 && $maxLocal !== null) {
            return max(0, (int)$maxLocal - (int)$margenIds);
        }

        return null;
    }

    /**
     * Fija la marca de agua de una vista.
     *
     * Hace falta para el modo OFFSET: si la ventana movil retrocede el cursor,
     * ese valor tiene que quedar guardado ANTES de empezar a paginar. En OFFSET
     * el filtro no puede cambiar a mitad del trabajo -- si un corte obliga a
     * retomar y el filtro fuera distinto, el OFFSET apuntaria a otras filas y
     * se saltearian registros.
     */
    public function setLastId($vistaNombre, $lastId)
    {
        try {
            $stmt = $this->pdo->prepare(
                "UPDATE `" . self::TABLE_CONTROL . "`
                    SET last_id = ?, updated_at = NOW()
                  WHERE vista_nombre = ?"
            );
            $stmt->execute(array((int)$lastId, $vistaNombre));
        } catch (PDOException $e) {
            $this->logger->warning("No se pudo fijar last_id de '" . $vistaNombre . "': " . $e->getMessage());
        }
    }

    /**
     * Obtiene MAX(idpk) de la tabla staging.
     * Usado para guardar el punto exacto de retoma.
     */
    public function getMaxStagingId($tablaLocal, $pkColumn)
    {
        $stagingName = $tablaLocal . '_staging';
        return $this->getMaxLocalId($stagingName, $pkColumn);
    }

    // ── Queue: cola de trabajos ───────────────────────────────────────────────

    public function enqueue($vistaNombre, $tablaLocal, $endpointUrl, $idpkParam, $priority = 0)
    {
        // No duplicar si ya esta activo
        $stmt = $this->pdo->prepare(
            "SELECT id, status FROM `" . self::TABLE_QUEUE . "`
             WHERE vista_nombre = ? AND status IN ('pending','running','paused')
             ORDER BY id DESC LIMIT 1"
        );
        $stmt->execute(array($vistaNombre));
        $existing = $stmt->fetch(PDO::FETCH_ASSOC);

        if ($existing) {
            return array(
                'job_id'  => $existing['id'],
                'status'  => $existing['status'],
                'queued'  => false,
                'message' => "Ya en cola con estado: " . $existing['status'],
            );
        }

        $stmt = $this->pdo->prepare(
            "INSERT INTO `" . self::TABLE_QUEUE . "`
             (vista_nombre, tabla_local, endpoint_url, idpk_param, status, priority, updated_at)
             VALUES (?, ?, ?, ?, 'pending', ?, NOW())"
        );
        $stmt->execute(array($vistaNombre, $tablaLocal, $endpointUrl, $idpkParam, $priority));
        $jobId = (int)$this->pdo->lastInsertId();

        return array(
            'job_id'  => $jobId,
            'status'  => 'pending',
            'queued'  => true,
            'message' => "Encolado correctamente.",
        );
    }

    public function getNextPending($workerTimeoutMinutes = 120)
    {
        $this->cleanStaleJobs($workerTimeoutMinutes);

        $stmt = $this->pdo->prepare(
            "SELECT * FROM `" . self::TABLE_QUEUE . "`
             WHERE status = 'pending'
             ORDER BY priority DESC, id ASC
             LIMIT 1"
        );
        $stmt->execute();
        return $stmt->fetch(PDO::FETCH_ASSOC);
    }

    public function getPausedJob($vistaNombre)
    {
        $stmt = $this->pdo->prepare(
            "SELECT * FROM `" . self::TABLE_QUEUE . "`
             WHERE vista_nombre = ? AND status = 'paused'
             ORDER BY id DESC LIMIT 1"
        );
        $stmt->execute(array($vistaNombre));
        return $stmt->fetch(PDO::FETCH_ASSOC);
    }

    /**
     * Obtiene un job por su ID, sin importar el estado.
     * Usado para leer el checkpoint de retoma (current_page, records_downloaded,
     * last_staging_id): al retomar, markJobRunning() ya movio el job a 'running',
     * asi que getPausedJob no lo encontraria. Esas columnas de progreso no las
     * toca markJobRunning, por lo que siguen siendo el punto exacto de corte.
     */
    public function getJobById($jobId)
    {
        $stmt = $this->pdo->prepare(
            "SELECT * FROM `" . self::TABLE_QUEUE . "` WHERE id = ? LIMIT 1"
        );
        $stmt->execute(array($jobId));
        return $stmt->fetch(PDO::FETCH_ASSOC);
    }

    public function getActiveJob($vistaNombre)
    {
        $stmt = $this->pdo->prepare(
            "SELECT * FROM `" . self::TABLE_QUEUE . "`
             WHERE vista_nombre = ? AND status IN ('pending','running','paused')
             ORDER BY id DESC LIMIT 1"
        );
        $stmt->execute(array($vistaNombre));
        return $stmt->fetch(PDO::FETCH_ASSOC);
    }

    public function markJobRunning($jobId)
    {
        $stmt = $this->pdo->prepare(
            "UPDATE `" . self::TABLE_QUEUE . "`
             SET status = 'running', worker_started_at = NOW(),
                 worker_heartbeat = NOW(), updated_at = NOW()
             WHERE id = ?"
        );
        $stmt->execute(array($jobId));
    }

    /**
     * Actualiza progreso del worker.
     * Guarda last_staging_id para retoma exacta en caso de corte.
     *
     * @param int      $jobId
     * @param int      $currentPage       Pagina actual
     * @param int      $recordsDownloaded Total descargado hasta ahora
     * @param int|null $lastStagingId     MAX(idpk) en staging (null si no hay idpk)
     */
    public function updateHeartbeat($jobId, $currentPage, $recordsDownloaded, $lastStagingId = null)
    {
        $stmt = $this->pdo->prepare(
            "UPDATE `" . self::TABLE_QUEUE . "`
             SET worker_heartbeat    = NOW(),
                 current_page        = ?,
                 records_downloaded  = ?,
                 last_staging_id     = ?,
                 has_staging         = 1,
                 updated_at          = NOW()
             WHERE id = ?"
        );
        $stmt->execute(array($currentPage, $recordsDownloaded, $lastStagingId, $jobId));
    }

    /**
     * Pausa un job — puede ser por solicitud del usuario o por error de red.
     *
     * @param int    $jobId
     * @param string $reason  'user' | 'network_error' | 'timeout'
     */
    public function pauseJob($jobId, $reason = 'user')
    {
        $stmt = $this->pdo->prepare(
            "UPDATE `" . self::TABLE_QUEUE . "`
             SET status = 'paused', pause_reason = ?, updated_at = NOW()
             WHERE id = ? AND status = 'running'"
        );
        $stmt->execute(array($reason, $jobId));
        return $stmt->rowCount() > 0;
    }

    public function resumeJob($jobId)
    {
        $stmt = $this->pdo->prepare(
            "UPDATE `" . self::TABLE_QUEUE . "`
             SET status = 'pending', pause_reason = NULL, updated_at = NOW()
             WHERE id = ? AND status = 'paused'"
        );
        $stmt->execute(array($jobId));
        return $stmt->rowCount() > 0;
    }

    /**
     * Auto-retoma jobs pausados por causa TRANSITORIA (timeout de consulta,
     * error de red, horario de carga del backend) tras un periodo de espera.
     *
     * NO toca pausas manuales ('user', 'force_cancel'): esas requieren accion
     * explicita del usuario. Solo actua sobre jobs con staging (has_staging=1),
     * que son los que pueden retomar sin perder progreso.
     *
     * Deja pause_reason intacto (trazabilidad); se sobreescribe si vuelve a
     * pausarse. El job vuelve a 'pending' y el worker lo retoma como resume.
     *
     * @param  int $cooldownMinutes  Minutos a esperar antes de re-encolar
     * @return int Cantidad de jobs re-encolados
     */
    public function autoResumeTransientPaused($cooldownMinutes = 10)
    {
        $stmt = $this->pdo->prepare(
            "UPDATE `" . self::TABLE_QUEUE . "`
             SET status = 'pending', updated_at = NOW()
             WHERE status         = 'paused'
               AND has_staging    = 1
               AND pause_reason IN ('network_error','timeout')
               AND updated_at < DATE_SUB(NOW(), INTERVAL ? MINUTE)"
        );
        $stmt->execute(array((int)$cooldownMinutes));
        return $stmt->rowCount();
    }

    public function restartJob($vistaNombre)
    {
        $stmt = $this->pdo->prepare(
            "UPDATE `" . self::TABLE_QUEUE . "`
             SET status = 'cancelled', updated_at = NOW()
             WHERE vista_nombre = ? AND status IN ('pending','running','paused')"
        );
        $stmt->execute(array($vistaNombre));
    }

    public function markJobDone($jobId, $pages, $inserted, $updated, $elapsed)
    {
        $stmt = $this->pdo->prepare(
            "UPDATE `" . self::TABLE_QUEUE . "`
             SET status = 'done', has_staging = 0,
                 last_staging_id = NULL,
                 current_page = ?, records_downloaded = ?,
                 updated_at = NOW()
             WHERE id = ?"
        );
        $stmt->execute(array($pages, $inserted + $updated, $jobId));

        $this->addHistory(
            $this->getJobVista($jobId),
            $pages, $inserted, $updated, $elapsed, 'ok', null
        );
    }

    public function markJobError($jobId, $errorMessage)
    {
        $stmt = $this->pdo->prepare(
            "UPDATE `" . self::TABLE_QUEUE . "`
             SET status = 'error', error_message = ?,
                 has_staging = 0, last_staging_id = NULL,
                 updated_at = NOW()
             WHERE id = ?"
        );
        $stmt->execute(array(substr($errorMessage, 0, 2000), $jobId));

        $this->addHistory(
            $this->getJobVista($jobId),
            0, 0, 0, 0, 'error', $errorMessage
        );
    }

    public function isRunning($vistaNombre)
    {
        $stmt = $this->pdo->prepare(
            "SELECT COUNT(*) FROM `" . self::TABLE_QUEUE . "`
             WHERE vista_nombre = ? AND status = 'running'"
        );
        $stmt->execute(array($vistaNombre));
        return (int)$stmt->fetchColumn() > 0;
    }

    public function getQueueStatus()
    {
        $stmt = $this->pdo->query(
            "SELECT * FROM `" . self::TABLE_QUEUE . "`
             WHERE status IN ('pending','running','paused')
             ORDER BY priority DESC, id ASC"
        );
        return $stmt->fetchAll(PDO::FETCH_ASSOC);
    }

    public function getHistory($vistaNombre, $limit = 20)
    {
        $stmt = $this->pdo->prepare(
            "SELECT * FROM `" . self::TABLE_HISTORY . "`
             WHERE vista_nombre = ?
             ORDER BY started_at DESC
             LIMIT " . (int)$limit
        );
        $stmt->execute(array($vistaNombre));
        return $stmt->fetchAll(PDO::FETCH_ASSOC);
    }

    // ── Worker control ────────────────────────────────────────────────────────

    public function workerHeartbeat()
    {
        $stmt = $this->pdo->prepare(
            "INSERT INTO `" . self::TABLE_CONTROL . "`
             (vista_nombre, tabla_local, endpoint_url, idpk_param,
              last_sync_status, last_sync_end, updated_at)
             VALUES ('__worker__', '', '', '', 'ok', NOW(), NOW())
             ON DUPLICATE KEY UPDATE last_sync_end = NOW(), updated_at = NOW()"
        );
        $stmt->execute();
    }

    public function getWorkerLastRun()
    {
        $stmt = $this->pdo->prepare(
            "SELECT last_sync_end FROM `" . self::TABLE_CONTROL . "`
             WHERE vista_nombre = '__worker__' LIMIT 1"
        );
        $stmt->execute();
        $row = $stmt->fetch(PDO::FETCH_ASSOC);
        return $row ? $row['last_sync_end'] : null;
    }

    // ── Internals ─────────────────────────────────────────────────────────────

    /**
     * Limpia jobs con heartbeat vencido.
     *
     * REGLA CLAVE:
     *   has_staging = 1 → pausar (no error) — se puede retomar
     *   has_staging = 0 → error — no habia progreso guardado
     */
    private function cleanStaleJobs($timeoutMinutes)
    {
        // Jobs con staging activa → pausa con razon timeout
        $stmtPause = $this->pdo->prepare(
            "UPDATE `" . self::TABLE_QUEUE . "`
             SET status       = 'paused',
                 pause_reason = 'timeout',
                 updated_at   = NOW()
             WHERE status           = 'running'
               AND has_staging      = 1
               AND worker_heartbeat < DATE_SUB(NOW(), INTERVAL ? MINUTE)"
        );
        $stmtPause->execute(array($timeoutMinutes));
        $paused = $stmtPause->rowCount();

        // Jobs sin staging → error (no habia nada que conservar)
        $stmtError = $this->pdo->prepare(
            "UPDATE `" . self::TABLE_QUEUE . "`
             SET status        = 'error',
                 error_message = 'Worker timeout — proceso interrumpido sin progreso guardado',
                 updated_at    = NOW()
             WHERE status           = 'running'
               AND has_staging      = 0
               AND worker_heartbeat < DATE_SUB(NOW(), INTERVAL ? MINUTE)"
        );
        $stmtError->execute(array($timeoutMinutes));
        $errored = $stmtError->rowCount();

        if ($paused > 0) {
            $this->logger->warning(
                "Worker timeout: " . $paused . " job(s) pausados (tienen staging — se pueden retomar)."
            );
        }
        if ($errored > 0) {
            $this->logger->warning(
                "Worker timeout: " . $errored . " job(s) marcados como error (sin staging)."
            );
        }
    }

    private function addHistory($vistaNombre, $pages, $inserted, $updated, $elapsed, $status, $error)
    {
        if (empty($vistaNombre) || $vistaNombre === '__worker__') return;

        $stmt = $this->pdo->prepare(
            "INSERT INTO `" . self::TABLE_HISTORY . "`
             (vista_nombre, started_at, finished_at, status,
              pages_downloaded, records_inserted, records_updated,
              elapsed_seconds, error_message)
             VALUES (?, NOW(), NOW(), ?, ?, ?, ?, ?, ?)"
        );
        $stmt->execute(array(
            $vistaNombre, $status, $pages, $inserted, $updated,
            round($elapsed, 2),
            $error ? substr($error, 0, 2000) : null,
        ));
    }

    private function getJobVista($jobId)
    {
        $stmt = $this->pdo->prepare(
            "SELECT vista_nombre FROM `" . self::TABLE_QUEUE . "` WHERE id = ? LIMIT 1"
        );
        $stmt->execute(array($jobId));
        $row = $stmt->fetch(PDO::FETCH_ASSOC);
        return $row ? $row['vista_nombre'] : '';
    }
}
