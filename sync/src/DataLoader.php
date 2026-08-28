<?php
/**
 * DataLoader.php
 * Inserta registros en la tabla staging usando batching y transacciones.
 * Compatible con PHP 5.5+
 */

class DataLoader
{
    private $pdo;
    private $logger;
    private $schema;

    const BATCH_SIZE = 500;

    public function __construct($pdo, $logger, $schema)
    {
        $this->pdo    = $pdo;
        $this->logger = $logger;
        $this->schema = $schema;
    }

    /**
     * Inserta un lote de registros en la tabla staging.
     *
     * @param  string $stagingTable  Nombre de la staging table
     * @param  array  $sanitizedSchema Schema sanitizado ['col' => 'type']
     * @param  array  $data          Registros del backend (nombres originales)
     * @param  array  $columnMap     Mapa original → sanitizado
     * @return int    Registros insertados
     */
    public function insertBatch($stagingTable, $sanitizedSchema, $data, $columnMap)
    {
        if (empty($data)) return 0;

        $total   = 0;
        $columns = array_keys($sanitizedSchema);
        $batches = array_chunk($data, self::BATCH_SIZE);

        foreach ($batches as $batch) {
            $total += $this->processBatch($stagingTable, $columns, $batch, $columnMap);
        }

        return $total;
    }

    // ── Batch individual ──────────────────────────────────────────────────────

    private function processBatch($stagingTable, $columns, $batch, $columnMap)
    {
        $quotedTable = $this->schema->qi($stagingTable);

        // Columnas quoted (sin row_id — es autoincremental)
        $quotedCols = array();
        foreach ($columns as $c) {
            $quotedCols[] = $this->schema->qi($c);
        }

        // Una fila: (?, ?, ...)
        $rowPlaceholders = '(' . implode(', ', array_fill(0, count($columns), '?')) . ')';

        $sql = "INSERT INTO {$quotedTable} (" . implode(', ', $quotedCols) . ")\n"
             . "VALUES\n"
             . implode(",\n", array_fill(0, count($batch), $rowPlaceholders));

        // Array plano de valores
        $values = array();
        foreach ($batch as $record) {
            foreach ($columns as $sanitizedCol) {
                $originalCol = $this->findOriginal($sanitizedCol, $columnMap);
                $values[]    = $this->extractValue($record, $originalCol, $sanitizedCol);
            }
        }

        $this->pdo->beginTransaction();

        try {
            $stmt = $this->pdo->prepare($sql);
            $stmt->execute($values);
            $inserted = count($batch);
            $this->pdo->commit();
            return $inserted;

        } catch (PDOException $e) {
            $this->pdo->rollBack();
            $this->logger->error("Error en batch INSERT staging: " . $e->getMessage());
            throw new RuntimeException("Error al insertar en staging: " . $e->getMessage(), 0, $e);
        }
    }

    // ── Utilidades ────────────────────────────────────────────────────────────

    private function findOriginal($sanitizedCol, $columnMap)
    {
        $flipped = array_flip($columnMap);
        return isset($flipped[$sanitizedCol]) ? $flipped[$sanitizedCol] : $sanitizedCol;
    }

    private function extractValue($record, $originalCol, $sanitizedCol)
    {
        if (array_key_exists($originalCol, $record)) {
            return $this->normalizeValue($record[$originalCol]);
        }

        if (array_key_exists($sanitizedCol, $record)) {
            return $this->normalizeValue($record[$sanitizedCol]);
        }

        return null;
    }

    private function normalizeValue($value)
    {
        if ($value === '' || $value === 'null') {
            return null;
        }

        if (is_array($value) || is_object($value)) {
            return json_encode($value);
        }

        return $value;
    }
}
