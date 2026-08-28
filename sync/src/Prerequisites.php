<?php
/**
 * Prerequisites.php
 * Verificacion de prerequisitos del sistema.
 * Detecta WAMP/XAMPP, version PHP, extensiones, permisos.
 * Compatible con PHP 5.5+
 */

class Prerequisites
{
    private $biRoot;
    private $results = array();

    public function __construct($biRoot)
    {
        $this->biRoot = $biRoot;
    }

    /**
     * Ejecuta todos los chequeos.
     * Retorna array de resultados.
     */
    public function check()
    {
        $this->results = array();

        $this->checkPhpVersion();
        $this->checkExtensions();
        $this->checkMysqlConnection();
        $this->checkLogPermissions();
        $this->checkConfigFile();
        $this->detectEnvironment();

        return $this->results;
    }

    /**
     * Retorna true si todos los chequeos criticos pasaron
     */
    public function allOk()
    {
        foreach ($this->results as $r) {
            if ($r['critical'] && !$r['ok']) {
                return false;
            }
        }
        return true;
    }

    // ── Chequeos individuales ─────────────────────────────────────────────────

    private function checkPhpVersion()
    {
        $version  = PHP_VERSION;
        $ok       = version_compare($version, '5.5.0', '>=');
        $message  = $ok
            ? "PHP " . $version . " — OK"
            : "PHP " . $version . " — Se requiere PHP 5.5 o superior";

        $this->add('php_version', 'Version de PHP', $ok, $message, true);
    }

    private function checkExtensions()
    {
        $extensions = array(
            'pdo'        => array('name' => 'PDO',        'critical' => true),
            'pdo_mysql'  => array('name' => 'PDO MySQL',  'critical' => true),
            'curl'       => array('name' => 'CURL',        'critical' => true),
            'json'       => array('name' => 'JSON',        'critical' => true),
            'mbstring'   => array('name' => 'mbstring',    'critical' => false),
            'zlib'       => array('name' => 'zlib (gzip)', 'critical' => false),
        );

        foreach ($extensions as $ext => $info) {
            $loaded  = extension_loaded($ext);
            $fix     = '';

            if (!$loaded) {
                $fix = $this->getExtensionFix($ext);
            }

            $msg = $loaded
                ? $info['name'] . " — habilitado"
                : $info['name'] . " — NO habilitado. " . $fix;

            $this->add('ext_' . $ext, $info['name'], $loaded, $msg, $info['critical']);
        }
    }

    private function checkMysqlConnection()
    {
        $configPath = $this->biRoot . '/config.txt';

        if (!file_exists($configPath)) {
            $this->add('mysql', 'Conexion MySQL', false, 'config.txt no encontrado — no se puede probar conexion', false);
            return;
        }

        try {
            $config = new Config($configPath);
            $result = Database::test($config->getDbConfig());

            if ($result['ok']) {
                $this->add('mysql', 'Conexion MySQL', true, 'Conexion exitosa a la base de datos', true);
            } else {
                $this->add('mysql', 'Conexion MySQL', false, 'Error: ' . $result['error'], true);
            }
        } catch (Exception $e) {
            $this->add('mysql', 'Conexion MySQL', false, 'Error: ' . $e->getMessage(), true);
        }
    }

    private function checkLogPermissions()
    {
        $logsDir = $this->biRoot . '/logs';

        if (!is_dir($logsDir)) {
            $created = mkdir($logsDir, 0755, true);
            if (!$created) {
                $this->add('logs', 'Permisos de logs', false,
                    'No se pudo crear directorio /logs — verificar permisos de escritura en ' . $this->biRoot, true);
                return;
            }
        }

        $testFile = $logsDir . '/.write_test';
        $canWrite = @file_put_contents($testFile, 'test') !== false;

        if ($canWrite) {
            @unlink($testFile);
            $this->add('logs', 'Permisos de logs', true, 'Directorio /logs con permisos de escritura', false);
        } else {
            $this->add('logs', 'Permisos de logs', false,
                'Sin permisos de escritura en /logs — hacer clic derecho en la carpeta → Propiedades → Seguridad', true);
        }
    }

    private function checkConfigFile()
    {
        $configPath = $this->biRoot . '/config.txt';

        if (!file_exists($configPath)) {
            $this->add('config', 'Archivo config.txt', false,
                'No encontrado — descargar config.txt del ERP y copiarlo en ' . $this->biRoot, true);
            return;
        }

        try {
            $config = new Config($configPath);
            $errors = $config->validate();

            if (empty($errors)) {
                $this->add('config', 'Archivo config.txt', true, 'Configuracion valida', false);
            } else {
                $this->add('config', 'Archivo config.txt', false,
                    'Configuracion incompleta: ' . implode(', ', $errors), true);
            }
        } catch (Exception $e) {
            $this->add('config', 'Archivo config.txt', false, 'Error al leer: ' . $e->getMessage(), true);
        }
    }

    private function detectEnvironment()
    {
        $env  = $this->getEnvironment();
        $msg  = "Entorno detectado: " . $env['name'];

        if (!empty($env['php_cli'])) {
            $msg .= " | PHP CLI: " . $env['php_cli'];
        }

        $this->add('environment', 'Entorno', true, $msg, false, $env);
    }

    // ── Deteccion de entorno ──────────────────────────────────────────────────

    public function getEnvironment()
    {
        // WAMP
        if (file_exists('C:\\wamp\\bin\\php')) {
            $phpDirs = glob('C:\\wamp\\bin\\php\\php*', GLOB_ONLYDIR);
            $phpCli  = '';

            if (!empty($phpDirs)) {
                rsort($phpDirs);
                $phpCli = $phpDirs[0] . '\\php.exe';
            }

            return array(
                'name'       => 'WAMP',
                'php_cli'    => $phpCli,
                'worker_cmd' => '"' . $phpCli . '" "' . $this->biRoot . '\\worker.php"',
                'ini_cli'    => !empty($phpDirs) ? $phpDirs[0] . '\\php.ini' : '',
                'type'       => 'wamp',
            );
        }

        // WAMP64
        if (file_exists('C:\\wamp64\\bin\\php')) {
            $phpDirs = glob('C:\\wamp64\\bin\\php\\php*', GLOB_ONLYDIR);
            $phpCli  = '';

            if (!empty($phpDirs)) {
                rsort($phpDirs);
                $phpCli = $phpDirs[0] . '\\php.exe';
            }

            return array(
                'name'       => 'WAMP64',
                'php_cli'    => $phpCli,
                'worker_cmd' => '"' . $phpCli . '" "' . $this->biRoot . '\\worker.php"',
                'ini_cli'    => !empty($phpDirs) ? $phpDirs[0] . '\\php.ini' : '',
                'type'       => 'wamp',
            );
        }

        // XAMPP
        if (file_exists('C:\\xampp\\php\\php.exe')) {
            $phpCli = 'C:\\xampp\\php\\php.exe';
            return array(
                'name'       => 'XAMPP',
                'php_cli'    => $phpCli,
                'worker_cmd' => '"' . $phpCli . '" "' . $this->biRoot . '\\worker.php"',
                'ini_cli'    => 'C:\\xampp\\php\\php.ini',
                'type'       => 'xampp',
            );
        }

        // Otro / Linux
        $phpCli = PHP_BINARY;
        return array(
            'name'       => 'Otro',
            'php_cli'    => $phpCli,
            'worker_cmd' => '"' . $phpCli . '" "' . $this->biRoot . '/worker.php"',
            'ini_cli'    => '',
            'type'       => 'other',
        );
    }

    // ── Fix hints para extensiones ────────────────────────────────────────────

    private function getExtensionFix($ext)
    {
        $env = $this->getEnvironment();

        $iniPath = !empty($env['ini_cli'])
            ? $env['ini_cli']
            : 'php.ini CLI';

        $fixes = array(
            'pdo_mysql' => "Abrir {$iniPath} y descomentar: extension=php_pdo_mysql.dll",
            'curl'      => "Abrir {$iniPath} y descomentar: extension=php_curl.dll",
            'mbstring'  => "Abrir {$iniPath} y descomentar: extension=php_mbstring.dll",
            'zlib'      => "Abrir {$iniPath} y descomentar: extension=php_zlib.dll",
        );

        return isset($fixes[$ext]) ? $fixes[$ext] : "Habilitar extension {$ext} en php.ini";
    }

    // ── Helper ────────────────────────────────────────────────────────────────

    private function add($key, $label, $ok, $message, $critical, $extra = array())
    {
        $this->results[$key] = array(
            'key'      => $key,
            'label'    => $label,
            'ok'       => (bool)$ok,
            'message'  => $message,
            'critical' => (bool)$critical,
            'extra'    => $extra,
        );
    }
}
