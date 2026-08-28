<?php
/**
 * SchemaManager.php
 * Gestion de esquema: creacion, ALTER TABLE, staging tables, merge.
 *
 * Cambios v2:
 * - mergeStagingToReal: sube innodb_lock_wait_timeout antes del merge
 *   y lo restaura despues para no afectar otras operaciones
 * - Retry en el merge si falla por lock timeout
 *
 * Compatible con PHP 5.5+ / MySQL 5.6+
 */

class SchemaManager
{
    private $pdo;
    private $logger;

    private static $typeMap = array(
        'int'      => 'BIGINT',
        'float'    => 'DOUBLE',
        'datetime' => 'DATETIME',
        'bool'     => 'TINYINT(1)',
        'string'   => 'LONGTEXT',
    );

    // Segundos para el lock durante el merge
    // Suficiente para tablas grandes sin bloquear indefinidamente
    const MERGE_LOCK_TIMEOUT = 300;

    public function __construct($pdo, $logger)
    {
        $this->pdo    = $pdo;
        $this->logger = $logger;
    }

    public function ensureTable($tableName, $idpkColumns, $schema)
    {
        if (!$this->tableExists($tableName)) {
            $this->createTable($tableName, $idpkColumns, $schema);
            return array_keys($schema);
        }
        return $this->syncColumns($tableName, $idpkColumns, $schema);
    }

    public function createStagingTable($tableName, $idpkColumns, $schema)
    {
        $stagingName = $tableName . '_staging';
        $this->dropStagingTable($tableName);
        $this->createTable($stagingName, $idpkColumns, $schema);
        $this->logger->info("Staging creada: " . $stagingName);
        return $stagingName;
    }

    public function dropStagingTable($tableName)
    {
        $stagingName = $tableName . '_staging';
        try {
            $this->pdo->exec("DROP TABLE IF EXISTS " . $this->qi($stagingName));
        } catch (PDOException $e) {
            $this->logger->warning("No se pudo eliminar staging: " . $e->getMessage());
        }
    }

    public function stagingExists($tableName)
    {
        return $this->tableExists($tableName . '_staging');
    }

    /**
     * Merge staging → tabla real dentro de una transaccion.
     * Sube innodb_lock_wait_timeout antes del merge para tablas grandes.
     * Reintenta una vez si hay lock timeout.
     */
    public function mergeStagingToReal($tableName, $idpkColumns, $schema, $mode = 'upsert')
    {
        $stagingName = $tableName . '_staging';

        if (!$this->tableExists($stagingName)) {
            throw new RuntimeException("Staging no existe: " . $stagingName);
        }

        $columns   = array_keys($schema);
        $idpkCols  = array_map('trim', explode(',', $idpkColumns));
        $firstIdpk = $idpkCols[0];

        $this->logger->info(
            "Merge staging → " . $tableName
            . " (modo: " . $mode . ", lock timeout: " . self::MERGE_LOCK_TIMEOUT . "s)"
        );

        // Subir lock timeout para el merge
        $prevLockTimeout = $this->getLockTimeout();
        $this->setLockTimeout(self::MERGE_LOCK_TIMEOUT);

        $maxRetries = 2;
        $attempt    = 0;

        while ($attempt < $maxRetries) {
            $attempt++;

            try {
                $this->pdo->beginTransaction();

                if ($mode === 'replace') {
                    $this->mergeReplace(
                        $this->qi($tableName),
                        $this->qi($stagingName),
                        $firstIdpk,
                        $columns
                    );
                } else {
                    $this->mergeUpsert(
                        $this->qi($tableName),
                        $this->qi($stagingName),
                        $firstIdpk,
                        $columns
                    );
                }

                $this->pdo->commit();
                $this->logger->success("Merge completado: " . $tableName);

                // Restaurar lock timeout
                $this->setLockTimeout($prevLockTimeout);
                return;

            } catch (PDOException $e) {
                if ($this->pdo->inTransaction()) {
                    $this->pdo->rollBack();
                }

                $isLockTimeout = (
                    strpos($e->getMessage(), 'Lock wait timeout') !== false ||
                    $e->errorInfo[1] == 1205
                );

                if ($isLockTimeout && $attempt < $maxRetries) {
                    $this->logger->warning(
                        "Lock timeout en merge (intento " . $attempt . ") — reintentando en 3s..."
                    );
                    sleep(3);
                    continue;
                }

                // Restaurar lock timeout antes de lanzar
                $this->setLockTimeout($prevLockTimeout);
                throw new RuntimeException("Error en merge: " . $e->getMessage());
            }
        }

        $this->setLockTimeout($prevLockTimeout);
        throw new RuntimeException("Merge fallo despues de " . $maxRetries . " intentos.");
    }

    // ── Merge strategies ──────────────────────────────────────────────────────

    private function mergeUpsert($quotedReal, $quotedStaging, $firstIdpk, $columns)
    {
        $quotedIdpk = $this->qi($firstIdpk);

        $colsList   = implode(', ', array_map(array($this, 'qi'), $columns));
        $colsSelect = implode(', ', array_map(function($c) {
            return 's.' . $this->qi($c);
        }, $columns));

        // INSERT nuevos
        $this->pdo->exec(
            "INSERT INTO {$quotedReal} ({$colsList})
             SELECT {$colsSelect}
             FROM {$quotedStaging} s
             LEFT JOIN {$quotedReal} r ON r.{$quotedIdpk} = s.{$quotedIdpk}
             WHERE r.{$quotedIdpk} IS NULL"
        );

        // UPDATE existentes
        $updateParts = array();
        foreach ($columns as $col) {
            if ($col !== $firstIdpk) {
                $qc            = $this->qi($col);
                $updateParts[] = "r.{$qc} = s.{$qc}";
            }
        }

        if (!empty($updateParts)) {
            $this->pdo->exec(
                "UPDATE {$quotedReal} r
                 INNER JOIN {$quotedStaging} s ON s.{$quotedIdpk} = r.{$quotedIdpk}
                 SET " . implode(', ', $updateParts)
            );
        }
    }

    private function mergeReplace($quotedReal, $quotedStaging, $firstIdpk, $columns)
    {
        $quotedIdpk = $this->qi($firstIdpk);

        // IDs unicos en staging
        $stmt = $this->pdo->query(
            "SELECT DISTINCT {$quotedIdpk} FROM {$quotedStaging}"
        );
        $ids = $stmt->fetchAll(PDO::FETCH_COLUMN);

        if (empty($ids)) return;

        // DELETE en real para esos IDs (en lotes de 1000 para no saturar)
        $chunks = array_chunk($ids, 1000);
        foreach ($chunks as $chunk) {
            $placeholders = implode(',', array_fill(0, count($chunk), '?'));
            $stmtDel      = $this->pdo->prepare(
                "DELETE FROM {$quotedReal} WHERE {$quotedIdpk} IN ({$placeholders})"
            );
            $stmtDel->execute($chunk);
        }

        // INSERT desde staging
        $colsList   = implode(', ', array_map(array($this, 'qi'), $columns));
        $colsSelect = implode(', ', array_map(array($this, 'qi'), $columns));

        $this->pdo->exec(
            "INSERT INTO {$quotedReal} ({$colsList})
             SELECT {$colsSelect} FROM {$quotedStaging}"
        );
    }

    // ── Crear tabla ───────────────────────────────────────────────────────────

    private function createTable($tableName, $idpkColumns, $schema)
    {
        $quotedTable = $this->qi($tableName);
        $idpkCols    = array_map('trim', explode(',', $idpkColumns));
        $cols        = array();

        // row_id siempre primero
        $cols[] = "  `row_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT";

        foreach ($schema as $colName => $type) {
            $mysqlType = $this->mapType($type);
            $cols[]    = "  " . $this->qi($colName) . " {$mysqlType} NULL";
        }

        $cols[] = "  PRIMARY KEY (`row_id`)";

        // INDEX en columnas idpk
        foreach ($idpkCols as $idpkCol) {
            if (!empty($idpkCol) && isset($schema[$idpkCol])) {
                $cols[] = "  INDEX " . $this->qi('idx_' . $idpkCol)
                        . " (" . $this->qi($idpkCol) . ")";
            }
        }

        // INDEX en columnas int (que no sean idpk)
        foreach ($schema as $colName => $type) {
            if (strtolower($type) === 'int' && !in_array($colName, $idpkCols)) {
                $cols[] = "  INDEX " . $this->qi('idx_' . $colName)
                        . " (" . $this->qi($colName) . ")";
            }
        }

        $sql = "CREATE TABLE IF NOT EXISTS {$quotedTable} (\n"
             . implode(",\n", $cols) . "\n"
             . ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci";

        try {
            $this->pdo->exec($sql);
        } catch (PDOException $e) {
            if (strpos($e->getMessage(), 'utf8mb4') !== false) {
                $sql = str_replace(
                    'utf8mb4 COLLATE=utf8mb4_unicode_ci',
                    'utf8 COLLATE=utf8_unicode_ci',
                    $sql
                );
                $this->pdo->exec($sql);
            } else {
                throw $e;
            }
        }

        $this->logger->success("Tabla '" . $tableName . "' creada.");
    }

    // ── Sincronizar columnas ──────────────────────────────────────────────────

    private function syncColumns($tableName, $idpkColumns, $schema)
    {
        $existing = $this->getExistingColumns($tableName);
        $added    = array();
        $idpkCols = array_map('trim', explode(',', $idpkColumns));

        foreach ($schema as $colName => $type) {
            $exists = in_array(
                strtolower($colName),
                array_map('strtolower', $existing),
                true
            );

            if (!$exists) {
                $this->addColumn($tableName, $colName, $type);
                $added[] = $colName;

                if (strtolower($type) === 'int' || in_array($colName, $idpkCols)) {
                    $this->addIndex($tableName, $colName);
                }
            }
        }

        return $added;
    }

    private function addColumn($tableName, $colName, $type)
    {
        $mysqlType = $this->mapType($type);
        $sql       = "ALTER TABLE " . $this->qi($tableName)
                   . " ADD COLUMN " . $this->qi($colName) . " {$mysqlType} NULL";

        $this->logger->info("ALTER TABLE: agregando '" . $colName . "' (" . $mysqlType . ") a '" . $tableName . "'");
        $this->pdo->exec($sql);
    }

    private function addIndex($tableName, $colName)
    {
        $idxName = 'idx_' . $colName;

        $stmt = $this->pdo->prepare(
            "SELECT COUNT(*) FROM information_schema.statistics
             WHERE table_schema = DATABASE()
               AND table_name   = ?
               AND index_name   = ?"
        );
        $stmt->execute(array($tableName, $idxName));

        if ((int)$stmt->fetchColumn() === 0) {
            try {
                $this->pdo->exec(
                    "ALTER TABLE " . $this->qi($tableName)
                    . " ADD INDEX " . $this->qi($idxName)
                    . " (" . $this->qi($colName) . ")"
                );
            } catch (PDOException $e) {
                // LONGTEXT no indexable — ignorar
                $this->logger->debug("No se agrego index en '" . $colName . "': " . $e->getMessage());
            }
        }
    }

    // ── Lock timeout helpers ──────────────────────────────────────────────────

    private function getLockTimeout()
    {
        try {
            $stmt = $this->pdo->query("SELECT @@SESSION.innodb_lock_wait_timeout");
            return (int)$stmt->fetchColumn();
        } catch (PDOException $e) {
            return 50; // default MySQL
        }
    }

    private function setLockTimeout($seconds)
    {
        try {
            $this->pdo->exec("SET SESSION innodb_lock_wait_timeout = " . (int)$seconds);
        } catch (PDOException $e) {
            // Ignorar si no soportado
        }
    }

    // ── Utilidades ────────────────────────────────────────────────────────────

    public function getExistingColumns($tableName)
    {
        $stmt = $this->pdo->prepare(
            "SELECT column_name FROM information_schema.columns
             WHERE table_schema = DATABASE()
               AND table_name   = ?
             ORDER BY ordinal_position"
        );
        $stmt->execute(array($tableName));
        return $stmt->fetchAll(PDO::FETCH_COLUMN);
    }

    public function tableExists($tableName)
    {
        $stmt = $this->pdo->prepare(
            "SELECT COUNT(*) FROM information_schema.tables
             WHERE table_schema = DATABASE()
               AND table_name   = ?"
        );
        $stmt->execute(array($tableName));
        return (int)$stmt->fetchColumn() > 0;
    }

    public function mapType($backendType)
    {
        $key = strtolower($backendType);
        return isset(self::$typeMap[$key]) ? self::$typeMap[$key] : 'LONGTEXT';
    }

    public function qi($name)
    {
        return '`' . str_replace('`', '``', $name) . '`';
    }

    public static function sanitizeName($name)
    {
        $name = preg_replace('/[\s\-]+/', '_', $name);
        $name = preg_replace('/[^a-zA-Z0-9_]/', '', $name);
        if (preg_match('/^[0-9]/', $name)) $name = 'col_' . $name;
        $name = substr($name, 0, 64);
        return empty($name) ? 'col_sin_nombre' : $name;
    }

    public static function sanitizeSchema($rawSchema)
    {
        $sanitized = array();
        foreach ($rawSchema as $colName => $type) {
            $clean             = self::sanitizeName((string)$colName);
            $sanitized[$clean] = (string)$type;
        }
        return $sanitized;
    }

    public static function buildColumnMap($rawSchema)
    {
        $map = array();
        foreach ($rawSchema as $colName => $type) {
            $map[(string)$colName] = self::sanitizeName((string)$colName);
        }
        return $map;
    }

    public static function sanitizeIdpk($idpk)
    {
        if (empty($idpk)) return '';
        $parts = explode(',', $idpk);
        $clean = array();
        foreach ($parts as $part) {
            $s = self::sanitizeName(trim($part));
            if (!empty($s)) $clean[] = $s;
        }
        return implode(',', $clean);
    }
}
