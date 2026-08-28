<?php
/**
 * Logger.php
 * Sistema de logging con soporte para archivo y SSE.
 * Compatible con PHP 5.5+
 */

class Logger
{
    private $logDir;
    private $logFile   = null;
    private $sseMode   = false;
    private $verbose   = false;

    const INFO    = 'INFO';
    const WARNING = 'WARNING';
    const ERROR   = 'ERROR';
    const DEBUG   = 'DEBUG';
    const SUCCESS = 'SUCCESS';

    public function __construct($logDir)
    {
        $this->logDir = rtrim($logDir, '/\\');

        if (!is_dir($this->logDir)) {
            mkdir($this->logDir, 0755, true);
        }
    }

    public function enableSSE()
    {
        $this->sseMode = true;
    }

    public function setVerbose($v)
    {
        $this->verbose = (bool)$v;
    }

    /**
     * Inicia archivo de log para esta sesion de sync
     */
    public function startSession($vistaNombre)
    {
        $slug          = preg_replace('/[^a-z0-9_]/i', '_', $vistaNombre);
        $ts            = date('Ymd_His');
        $this->logFile = $this->logDir . '/' . $slug . '_' . $ts . '.log';
        return $this->logFile;
    }

    public function info($msg, $context = array())
    {
        $this->write(self::INFO, $msg, $context);
    }

    public function success($msg, $context = array())
    {
        $this->write(self::SUCCESS, $msg, $context);
    }

    public function warning($msg, $context = array())
    {
        $this->write(self::WARNING, $msg, $context);
    }

    public function error($msg, $context = array())
    {
        $this->write(self::ERROR, $msg, $context);
    }

    public function debug($msg, $context = array())
    {
        if ($this->verbose) {
            $this->write(self::DEBUG, $msg, $context);
        }
    }

    /**
     * Envia evento SSE de progreso al navegador
     */
    public function progress($data)
    {
        if (!$this->sseMode) return;
        $this->sendSSE('progress', $data);
    }

    /**
     * Envia evento SSE de finalizacion
     */
    public function done($data)
    {
        if (!$this->sseMode) return;
        $this->sendSSE('done', $data);
    }

    /**
     * Envia evento SSE de error
     */
    public function sseError($message)
    {
        if (!$this->sseMode) return;
        $this->sendSSE('error', array('message' => $message));
    }

    /**
     * Lista ultimos N archivos de log de una vista
     */
    public function getRecentLogs($vistaNombre, $limit = 10)
    {
        $slug    = preg_replace('/[^a-z0-9_]/i', '_', $vistaNombre);
        $pattern = $this->logDir . '/' . $slug . '_*.log';
        $files   = glob($pattern);

        if ($files === false || empty($files)) {
            return array();
        }

        rsort($files);
        $files  = array_slice($files, 0, $limit);
        $result = array();

        foreach ($files as $file) {
            $result[] = array(
                'file'     => basename($file),
                'size'     => filesize($file),
                'modified' => date('Y-m-d H:i:s', filemtime($file)),
                'path'     => $file,
            );
        }

        return $result;
    }

    /**
     * Lee ultimas N lineas de un archivo de log
     */
    public function readLog($filePath, $lines = 100)
    {
        $realBase = realpath($this->logDir);
        $realFile = realpath($filePath);

        if ($realFile === false || strpos($realFile, $realBase) !== 0) {
            return "Acceso denegado.";
        }

        if (!file_exists($realFile)) {
            return "Archivo no encontrado.";
        }

        $content = file($realFile, FILE_IGNORE_NEW_LINES);
        if ($content === false) {
            return "No se pudo leer el archivo.";
        }

        $tail = array_slice($content, -$lines);
        return implode("\n", $tail);
    }

    // ── Internals ────────────────────────────────────────────────────────────

    private function write($level, $msg, $context)
    {
        $ts   = date('Y-m-d H:i:s');
        $ctx  = empty($context) ? '' : ' ' . json_encode($context);
        $line = "[{$ts}] [{$level}] {$msg}{$ctx}" . PHP_EOL;

        if ($this->logFile !== null) {
            file_put_contents($this->logFile, $line, FILE_APPEND | LOCK_EX);
        }

        if ($this->sseMode && in_array($level, array(self::INFO, self::SUCCESS, self::WARNING, self::ERROR))) {
            $this->sendSSE('log', array(
                'level'   => $level,
                'message' => $msg,
                'time'    => $ts,
            ));
        }
    }

    private function sendSSE($event, $data)
    {
        echo "event: {$event}\n";
        echo "data: " . json_encode($data) . "\n\n";
        if (ob_get_level() > 0) ob_flush();
        flush();
    }
}
