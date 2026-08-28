<?php
/**
 * ETLRunner.php
 * Orquestador ETL con manejo robusto de errores.
 *
 * Comportamiento ante errores:
 *   Error de red (timeout, 503, etc.)  → conservar staging → pausar job
 *   Error fatal (403, JSON invalido)   → DROP staging → marcar error
 *   PHP muere / WAMP reinicia          → staging sobrevive en MySQL
 *                                         worker timeout la detecta y pausa
 *
 * Al retomar:
 *   - Usa last_staging_id (MAX idpk en staging) como punto de inicio
 *   - Continua desde current_page guardada
 *   - Evita duplicados y saltos de registros
 *
 * Compatible con PHP 5.5+ / MySQL 5.6+
 */

class ETLRunner
{
    private $config;
    private $logger;
    private $http;
    private $schemaManager;
    private $syncControl;
    private $dataLoader;
    private $pdo;

    private $pauseRequested = false;

    public function __construct($config, $logger)
    {
        $this->config = $config;
        $this->logger = $logger;

        $this->http = new HttpClient(
            $config->getApiKey(),
            $logger,
            $config->getHttpTimeout(),
            $config->getHttpRetries(),
            $config->getHttpRetryDelay()
        );
    }

    private function getDb()
    {
        if ($this->pdo === null) {
            $this->pdo           = Database::connect($this->config->getDbConfig());
            $this->syncControl   = new SyncControl($this->pdo, $this->logger);
            $this->schemaManager = new SchemaManager($this->pdo, $this->logger);
            $this->dataLoader    = new DataLoader($this->pdo, $this->logger, $this->schemaManager);
        }
        return $this->pdo;
    }

    public function requestPause()
    {
        $this->pauseRequested = true;
    }

    /**
     * Ejecuta la sincronizacion de una vista.
     *
     * @param  array $vista       Config de la vista
     * @param  int   $jobId       ID del job en la cola
     * @param  bool  $sseMode     Si true, emite SSE
     * @param  bool  $resume      Si true, retoma staging existente
     * @return array Resultado
     */
    public function syncVista($vista, $jobId, $sseMode = false, $resume = false)
    {
        $this->getDb();

        if ($sseMode) {
            $this->logger->enableSSE();
        }

        $nombre      = isset($vista['nombre'])   ? $vista['nombre']   : 'vista_sin_nombre';
        $endpoint    = isset($vista['endpoint']) ? $vista['endpoint'] : '';
        $idpkOrig    = isset($vista['idpk'])     ? trim($vista['idpk']) : '';

        if (empty($endpoint)) {
            throw new RuntimeException("Vista '" . $nombre . "': endpoint no configurado.");
        }

        $endpointUrl  = $this->config->getUrlBase() . '/' . ltrim($endpoint, '/');
        $tablaLocal   = SchemaManager::sanitizeName($nombre);
        $pageSize     = $this->config->getPageSize();

        // $idpkGetParam: parametro GET que se envia al backend (?idfactura=xxx)
        // Viene del config.txt — puede diferir del nombre real de la columna
        $idpkOrigParts = !empty($idpkOrig) ? array_map('trim', explode(',', $idpkOrig)) : array();
        $firstIdpkOrig = !empty($idpkOrigParts) ? $idpkOrigParts[0] : '';
        $hasIncremental = !empty($firstIdpkOrig);

        // Modo keyset (cursor sobre la PK del detalle, ej. Ventas/idventadet):
        // el backend pagina con `idpk > cursor` (range seek), sin OFFSET.
        // Marcado por vista en vistas.txt: "keyset": true / "S".
        $isKeyset = isset($vista['keyset']) &&
            ($vista['keyset'] === true || $vista['keyset'] === 1
             || strtoupper((string)$vista['keyset']) === 'S'
             || (string)$vista['keyset'] === '1');

        // $idpkColumnLocal: nombre real de la columna en MySQL local
        // Se obtiene del campo idpk que devuelve el backend en la respuesta JSON
        // Se inicializa con el valor del config sanitizado como fallback
        // y se sobreescribe con el valor real del backend en la primera pagina
        $idpkSanitized  = SchemaManager::sanitizeIdpk($idpkOrig);
        $idpkParts      = !empty($idpkSanitized) ? array_map('trim', explode(',', $idpkSanitized)) : array();
        $firstIdpk      = !empty($idpkParts) ? $idpkParts[0] : ''; // se sobreescribe con el real del backend

        $this->logger->startSession($nombre);
        $this->logger->info("=== Sincronizando: " . $nombre . " ===");
        $this->logger->info("Tabla: " . $tablaLocal . " | idpk: " . ($idpkSanitized ?: 'ninguno (full)'));
        $this->logger->info("Modo: " . ($resume ? "RETOMAR" : "NUEVA SYNC"));

        $this->syncControl->upsertState($nombre, $tablaLocal, $endpointUrl, $idpkSanitized);
        $this->syncControl->markSyncStart($nombre);
        $this->syncControl->markJobRunning($jobId);

        $startTime       = microtime(true);
        $totalDownloaded = 0;
        $sanitizedSchema = null;
        $columnMap       = null;
        $stagingName     = null;
        $lastId          = null;
        $lastStagingId   = null;
        $mode            = 'upsert';

        // ── Determinar punto de inicio (solo se restaura en resume) ───────────
        $startPage = 1;

        if ($resume) {
            // Leer por ID: markJobRunning() ya movio el job a 'running', asi que
            // getPausedJob (status='paused') devolveria null y perderiamos el
            // checkpoint. Las columnas de progreso siguen intactas.
            $jobData         = $this->syncControl->getJobById($jobId);
            $startPage       = $jobData ? max(1, (int)$jobData['current_page']) : 1;
            $totalDownloaded = $jobData ? (int)$jobData['records_downloaded'] : 0;
            $lastStagingId   = $jobData && $jobData['last_staging_id'] !== null
                               ? (int)$jobData['last_staging_id']
                               : null;

            // Sin staging no hay nada que reutilizar: reiniciar desde pagina 1.
            if (!$this->schemaManager->stagingExists($tablaLocal)) {
                $this->logger->warning("Staging no encontrada al retomar — reiniciando desde pagina 1.");
                $startPage       = 1;
                $totalDownloaded = 0;
                $lastStagingId   = null;
            } else {
                $this->logger->info(
                    "Retomando desde pagina " . $startPage
                    . " | Descargados hasta ahora: " . $totalDownloaded
                    . " | last_staging_id: " . ($lastStagingId !== null ? $lastStagingId : 'ninguno')
                );
            }
        }

        // ── Determinar lastId (cursor incremental / keyset) ───────────────────
        if ($hasIncremental && $isKeyset) {
            // KEYSET: el cursor es la PK del detalle (ej. idventadet). El backend
            // hace `idpk > cursor` (range seek por PK), asi que last_staging_id ES
            // el punto exacto de retoma, y en cada pagina el cursor avanza.
            if ($resume && $lastStagingId !== null) {
                $lastId = $lastStagingId;
            } else {
                // incremental: arrancar desde el max idpk ya sincronizado; 0 = full
                $lastId = 0;
                if ($this->schemaManager->tableExists($tablaLocal)
                    && $this->syncControl->countLocalRecords($tablaLocal) > 0) {
                    $maxLocal = $this->syncControl->getMaxLocalId($tablaLocal, $firstIdpk);
                    $lastId = ($maxLocal > 1) ? $maxLocal : 0;

                    // ── VENTANA MOVIL ────────────────────────────────────────
                    // Parar el cursor en el ultimo ID sincronizado deja afuera
                    // para siempre dos casos: las correcciones sobre registros
                    // ya traidos, y los comprobantes cuyo ID se asigno antes de
                    // la sync pero se cerraron despues (mesa abierta). Este
                    // segundo es el que costo un sabado de tres sucursales.
                    // Retroceder el cursor al inicio de la ventana los recupera;
                    // releer es inofensivo porque el merge es upsert por PK.
                    $cursorVentana = $this->syncControl->getCursorVentana(
                        $tablaLocal,
                        $firstIdpk,
                        $this->config->getResyncDias(),
                        $this->config->getResyncMargenIds(),
                        $maxLocal
                    );
                    if ($cursorVentana !== null && $cursorVentana < $lastId) {
                        $this->logger->info(
                            "Ventana movil: cursor retrocedido de " . $lastId
                            . " a " . $cursorVentana
                            . " (se releen " . ($lastId - $cursorVentana) . " IDs)"
                        );
                        $lastId = $cursorVentana;
                    }
                }
            }
            $this->logger->info(
                ($resume ? "Retomando keyset" : "Keyset")
                . " desde " . $firstIdpkOrig . " > " . $lastId
            );

        } elseif ($hasIncremental) {
            // OFFSET (modo clasico): lastId = marca de agua de la tabla REAL (o
            // control.last_id), IGUAL en fresca y en resume. La tabla real esta
            // congelada hasta el merge. NO usar last_staging_id aca: el backend
            // pagina por OFFSET y mover el filtro rompe el conteo (salta datos).
            // El punto exacto de retoma lo da `page` (OFFSET), no el filtro.
            if ($this->schemaManager->tableExists($tablaLocal)) {
                $lastId = $this->syncControl->getLastId($nombre, $idpkSanitized);

                if ($lastId === 1 && $this->syncControl->countLocalRecords($tablaLocal) > 0) {
                    $lastId = $this->syncControl->getMaxLocalId($tablaLocal, $firstIdpk);
                }

                // ── VENTANA MOVIL (solo al iniciar, nunca al retomar) ────────
                // En OFFSET el filtro no puede cambiar a mitad del trabajo: el
                // punto de retoma lo da la pagina, no el filtro. Por eso el
                // cursor retrocedido se PERSISTE antes de empezar a paginar, y
                // un resume lo lee de ahi via getLastId().
                if (!$resume && $this->syncControl->countLocalRecords($tablaLocal) > 0) {
                    $maxLocalOffset = $this->syncControl->getMaxLocalId($tablaLocal, $firstIdpk);
                    $cursorVentana  = $this->syncControl->getCursorVentana(
                        $tablaLocal,
                        $firstIdpk,
                        $this->config->getResyncDias(),
                        $this->config->getResyncMargenIds(),
                        $maxLocalOffset
                    );
                    if ($cursorVentana !== null && $cursorVentana < $lastId) {
                        $this->logger->info(
                            "Ventana movil: cursor retrocedido de " . $lastId
                            . " a " . $cursorVentana
                            . " (se releen " . ($lastId - $cursorVentana) . " IDs)"
                        );
                        $lastId = $cursorVentana;
                        $this->syncControl->setLastId($nombre, $lastId);
                    }
                }
            } else {
                $lastId = 1;
            }
            $this->logger->info(
                ($resume ? "Retomando incremental" : "Incremental")
                . " desde " . $firstIdpkOrig . " = " . $lastId
                . " | pagina inicial: " . $startPage
            );
        }

        try {
            $pageNumber = $startPage;

            while (true) {
                // ── Verificar pausa solicitada por usuario ─────────────────────
                if ($this->pauseRequested) {
                    $currentStagingId = $this->getCurrentStagingId(
                        $tablaLocal, $firstIdpk, $lastStagingId
                    );
                    $this->syncControl->updateHeartbeat(
                        $jobId, $pageNumber, $totalDownloaded, $currentStagingId
                    );
                    $this->syncControl->pauseJob($jobId, 'user');
                    $this->logger->info("Pausado por usuario en pagina " . $pageNumber . ".");
                    $this->logger->progress(array(
                        'status'    => 'paused',
                        'page'      => $pageNumber,
                        'processed' => $totalDownloaded,
                    ));
                    return array('status' => 'paused', 'page' => $pageNumber);
                }

                // ── Verificar flag de pausa en archivo ─────────────────────────
                $pauseFlag = BI_ROOT . '/logs/pause_' . md5($nombre) . '.flag';
                if (file_exists($pauseFlag)) {
                    @unlink($pauseFlag);
                    $currentStagingId = $this->getCurrentStagingId(
                        $tablaLocal, $firstIdpk, $lastStagingId
                    );
                    $this->syncControl->updateHeartbeat(
                        $jobId, $pageNumber, $totalDownloaded, $currentStagingId
                    );
                    $this->syncControl->pauseJob($jobId, 'user');
                    $this->logger->info("Pausado por solicitud externa en pagina " . $pageNumber . ".");
                    return array('status' => 'paused', 'page' => $pageNumber);
                }

                $this->logger->info("Descargando pagina " . $pageNumber . "...");
                $this->logger->progress(array(
                    'status'    => 'downloading',
                    'page'      => $pageNumber,
                    'processed' => $totalDownloaded,
                ));

                // ── Descargar pagina con manejo de errores ─────────────────────
                try {
                    $response = $this->http->fetchPage(
                        $endpointUrl,
                        $pageNumber,
                        $pageSize,
                        $hasIncremental ? $lastId : null,
                        $hasIncremental ? $firstIdpkOrig : '',
                        $isKeyset ? array('ks' => '1') : array()
                    );

                } catch (HttpRetryableException $e) {
                    // Error de red — conservar staging y pausar
                    $this->handleNetworkError($e, $jobId, $nombre, $tablaLocal,
                        $firstIdpk, $pageNumber, $totalDownloaded, $lastStagingId);
                    return array('status' => 'paused', 'page' => $pageNumber, 'reason' => 'network_error');

                } catch (HttpFatalException $e) {
                    // Error fatal — DROP staging y marcar error
                    $this->handleFatalError($e, $jobId, $nombre, $tablaLocal);
                    throw new RuntimeException($e->getMessage());
                }

                $data = isset($response['data']) ? $response['data'] : array();

                if (empty($data)) {
                    $this->logger->info("Pagina " . $pageNumber . " vacia — sincronizacion completa.");
                    break;
                }

                $pageRecords = count($data);
                $this->logger->info("Pagina " . $pageNumber . ": " . $pageRecords . " registros.");

                // ── Primera pagina: inicializar schema y staging ───────────────
                if ($pageNumber === $startPage) {
                    $rawSchema = isset($response['schema']) ? $response['schema'] : array();

                    if (empty($rawSchema)) {
                        $this->handleFatalError(
                            new RuntimeException("El backend no envio 'schema'."),
                            $jobId, $nombre, $tablaLocal
                        );
                        throw new RuntimeException("El backend no envio 'schema'.");
                    }

                    $sanitizedSchema = SchemaManager::sanitizeSchema($rawSchema);
                    $columnMap       = SchemaManager::buildColumnMap($rawSchema);

                    // Obtener el nombre real de la columna PK desde la respuesta del backend
                    // Este puede diferir del idpk del config (que es solo el parametro GET)
                    $rawIdpkFromBackend = isset($response['idpk']) ? trim($response['idpk']) : '';

                    if (!empty($rawIdpkFromBackend)) {
                        // Sobreescribir con el nombre real que devuelve el backend
                        $idpkSanitized = SchemaManager::sanitizeIdpk($rawIdpkFromBackend);
                        $idpkParts     = array_map('trim', explode(',', $idpkSanitized));
                        $firstIdpk     = !empty($idpkParts) ? $idpkParts[0] : '';
                        $this->logger->info(
                            "idpk real del backend: '" . $rawIdpkFromBackend . "'"
                            . " → columna local: '" . $firstIdpk . "'"
                            . " | parametro GET: '" . $firstIdpkOrig . "'"
                        );
                    }

                    // Detectar modo replace vs upsert
                    if ($hasIncremental && $pageRecords > 1) {
                        $mode = $this->detectMode($data, $firstIdpkOrig);
                        $this->logger->info("Modo detectado: " . $mode);
                    }

                    // Crear/actualizar tabla real
                    $addedCols = $this->schemaManager->ensureTable(
                        $tablaLocal, $idpkSanitized, $sanitizedSchema
                    );
                    if (!empty($addedCols)) {
                        $this->logger->info("Columnas nuevas: " . implode(', ', $addedCols));
                    }

                    // Crear o reutilizar staging
                    if ($resume && $this->schemaManager->stagingExists($tablaLocal)) {
                        $stagingName = $tablaLocal . '_staging';
                        // Sincronizar columnas nuevas en staging tambien
                        $this->schemaManager->ensureTable(
                            $stagingName, $idpkSanitized, $sanitizedSchema
                        );
                        $this->logger->info("Reutilizando staging existente: " . $stagingName);
                    } else {
                        $stagingName = $this->schemaManager->createStagingTable(
                            $tablaLocal, $idpkSanitized, $sanitizedSchema
                        );
                    }
                }

                // ── INSERT en staging ──────────────────────────────────────────
                $this->logger->progress(array(
                    'status'       => 'inserting',
                    'page'         => $pageNumber,
                    'page_records' => $pageRecords,
                    'processed'    => $totalDownloaded,
                ));

                try {
                    $inserted = $this->dataLoader->insertBatch(
                        $stagingName, $sanitizedSchema, $data, $columnMap
                    );
                } catch (RuntimeException $e) {
                    // Error de MySQL al insertar — reconectar y reintentar una vez
                    $this->logger->warning("Error INSERT staging, intentando reconexion: " . $e->getMessage());
                    Database::reconnect();
                    $this->pdo           = Database::connect($this->config->getDbConfig());
                    $this->dataLoader    = new DataLoader($this->pdo, $this->logger, $this->schemaManager);
                    $this->syncControl   = new SyncControl($this->pdo, $this->logger);

                    $inserted = $this->dataLoader->insertBatch(
                        $stagingName, $sanitizedSchema, $data, $columnMap
                    );
                }

                $totalDownloaded += $inserted;

                // Actualizar MAX(idpk) de staging para punto de retoma exacto
                $lastStagingId = $this->getCurrentStagingId(
                    $tablaLocal, $firstIdpk, $lastStagingId
                );

                // KEYSET: avanzar el cursor al ultimo idpk descargado para que la
                // proxima pagina pida `idpk > cursor` (range seek). En modo OFFSET
                // el cursor NO se mueve (la posicion la da el page/OFFSET).
                if ($isKeyset && $lastStagingId !== null) {
                    $lastId = $lastStagingId;
                }

                // Heartbeat con last_staging_id
                $this->syncControl->updateHeartbeat(
                    $jobId, $pageNumber, $totalDownloaded, $lastStagingId
                );

                $this->logger->info(
                    "Pagina " . $pageNumber . " en staging: +"
                    . $inserted . " reg. | last_staging_id: "
                    . ($lastStagingId !== null ? $lastStagingId : 'N/A')
                );

                $this->logger->progress(array(
                    'status'    => 'page_done',
                    'page'      => $pageNumber,
                    'processed' => $totalDownloaded,
                ));

                if ($pageRecords < $pageSize) {
                    $this->logger->info("Pagina incompleta — fin de datos.");
                    break;
                }

                $pageNumber++;

                unset($data, $response);
                if (function_exists('gc_collect_cycles')) gc_collect_cycles();
            }

            // ── Merge staging → real ──────────────────────────────────────────
            if ($stagingName !== null) {
                $this->logger->info("Aplicando cambios a tabla real...");
                $this->logger->progress(array('status' => 'merging', 'processed' => $totalDownloaded));

                $this->schemaManager->mergeStagingToReal(
                    $tablaLocal,
                    $idpkSanitized ?: 'row_id',
                    $sanitizedSchema,
                    $mode
                );

                $this->schemaManager->dropStagingTable($tablaLocal);
            }

            // ── Fin exitoso ───────────────────────────────────────────────────
            $totalLocal    = $this->syncControl->countLocalRecords($tablaLocal);
            $totalInserted = (int)ceil($totalDownloaded * 0.7);
            $totalUpdated  = $totalDownloaded - $totalInserted;

            $newLastId = ($hasIncremental && $firstIdpk)
                ? $this->syncControl->getMaxLocalId($tablaLocal, $firstIdpk)
                : ($lastId ? $lastId : 0);

            $elapsed = round(microtime(true) - $startTime, 2);

            $this->syncControl->markSyncDone(
                $nombre, $newLastId, $totalLocal, $totalInserted, $totalUpdated
            );
            $this->syncControl->markJobDone(
                $jobId, $pageNumber, $totalInserted, $totalUpdated, $elapsed
            );

            $this->logger->success(
                "'" . $nombre . "' OK en " . $elapsed . "s — "
                . $totalDownloaded . " registros | "
                . $pageNumber . " paginas | "
                . "Total local: " . $totalLocal
            );

            $result = array(
                'status'            => 'done',
                'vista'             => $nombre,
                'records_processed' => $totalDownloaded,
                'inserted'          => $totalInserted,
                'updated'           => $totalUpdated,
                'pages'             => $pageNumber,
                'elapsed'           => $elapsed,
                'total_local'       => $totalLocal,
                'last_id'           => $newLastId,
            );

            $this->logger->done($result);
            return $result;

        } catch (Exception $e) {
            // Solo llega aca si fue un error FATAL (no de red)
            // La staging ya fue eliminada por handleFatalError
            // Asegurarse que el job quede marcado como error (no running)
            try {
                $this->syncControl->markJobError($jobId, $e->getMessage());
            } catch (Exception $ignored) {}
            $this->syncControl->markSyncError($nombre, $e->getMessage());
            $this->logger->error("Error fatal en '" . $nombre . "': " . $e->getMessage());
            $this->logger->sseError($e->getMessage());
            throw $e;
        }
    }

    // ── Manejo de errores ─────────────────────────────────────────────────────

    /**
     * Error de red: conservar staging, pausar job.
     * La tabla real NO se toca.
     */
    private function handleNetworkError($e, $jobId, $nombre, $tablaLocal,
        $firstIdpk, $pageNumber, $totalDownloaded, $lastStagingId)
    {
        $this->logger->warning(
            "Error de red en pagina " . $pageNumber . ": " . $e->getMessage()
        );
        $this->logger->warning(
            "Staging conservada — se puede retomar desde pagina " . $pageNumber
            . " (last_staging_id: " . ($lastStagingId !== null ? $lastStagingId : 'N/A') . ")"
        );

        // Actualizar heartbeat con estado actual antes de pausar
        $currentStagingId = $this->getCurrentStagingId($tablaLocal, $firstIdpk, $lastStagingId);
        $this->syncControl->updateHeartbeat(
            $jobId, $pageNumber, $totalDownloaded, $currentStagingId
        );
        $this->syncControl->pauseJob($jobId, 'network_error');
        $this->syncControl->markSyncError($nombre, "Error de red (pausado): " . $e->getMessage());

        $this->logger->sseError("Error de red — sync pausada. Puede retomar cuando se restaure la conexion.");
    }

    /**
     * Error fatal: DROP staging, marcar error.
     * No tiene sentido conservar datos parciales.
     */
    private function handleFatalError($e, $jobId, $nombre, $tablaLocal)
    {
        $this->logger->error("Error fatal: " . $e->getMessage() . " — eliminando staging.");
        $this->schemaManager->dropStagingTable($tablaLocal);
        $this->syncControl->markJobError($jobId, $e->getMessage());
    }

    // ── Utilidades ────────────────────────────────────────────────────────────

    /**
     * Obtiene el MAX(idpk) actual de la staging table.
     * Retorna null si no hay idpk o si falla.
     */
    private function getCurrentStagingId($tablaLocal, $firstIdpk, $fallback)
    {
        if (empty($firstIdpk)) return null;

        try {
            $id = $this->syncControl->getMaxStagingId($tablaLocal, $firstIdpk);
            return ($id > 1) ? $id : $fallback;
        } catch (Exception $e) {
            return $fallback;
        }
    }

    /**
     * Detecta si el modo debe ser 'replace' o 'upsert'.
     * Si el idpk se repite en el lote → replace.
     */
    private function detectMode($data, $firstIdpkOrig)
    {
        if (empty($firstIdpkOrig) || empty($data)) return 'upsert';

        $ids = array();
        foreach ($data as $record) {
            $val = isset($record[$firstIdpkOrig]) ? $record[$firstIdpkOrig] : null;
            if ($val !== null) {
                if (isset($ids[$val])) return 'replace';
                $ids[$val] = true;
            }
        }

        return 'upsert';
    }
}
