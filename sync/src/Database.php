<?php
/**
 * Database.php
 * Factory de conexion PDO con reconexion automatica.
 * Compatible con PHP 5.5+ / MySQL 5.6+
 */

class Database
{
    private static $instance = null;
    private static $dbConfig = null;

    public static function connect($dbConfig)
    {
        self::$dbConfig = $dbConfig;

        if (self::$instance !== null) {
            if (!self::ping()) {
                self::$instance = null;
            } else {
                return self::$instance;
            }
        }

        return self::createConnection($dbConfig);
    }

    /**
     * Reconecta usando la ultima configuracion conocida.
     * Llamado automaticamente si se detecta "MySQL server has gone away".
     */
    public static function reconnect()
    {
        if (self::$dbConfig === null) {
            throw new RuntimeException("No hay configuracion de DB para reconectar.");
        }
        self::$instance = null;
        return self::createConnection(self::$dbConfig);
    }

    /**
     * Ejecuta un callback con reconexion automatica si MySQL cayo.
     *
     * Uso:
     *   $result = Database::withReconnect(function($pdo) {
     *       $stmt = $pdo->query("SELECT ...");
     *       return $stmt->fetchAll();
     *   });
     */
    public static function withReconnect($callback, $maxRetries = 2)
    {
        $attempt = 0;

        while (true) {
            try {
                if (self::$instance === null) {
                    self::reconnect();
                }
                return call_user_func($callback, self::$instance);

            } catch (PDOException $e) {
                $msg = $e->getMessage();
                $code = isset($e->errorInfo[1]) ? (int)$e->errorInfo[1] : 0;

                // Detectar conexion perdida
                $isGoneAway = (
                    strpos($msg, 'MySQL server has gone away') !== false ||
                    strpos($msg, 'Lost connection')           !== false ||
                    strpos($msg, 'errno=32')                 !== false ||
                    strpos($msg, 'errno=104')                !== false ||
                    $code === 2006 ||
                    $code === 2013
                );

                if ($isGoneAway && $attempt < $maxRetries) {
                    self::$instance = null;
                    $attempt++;
                    sleep(1);
                    continue;
                }

                throw $e;
            }
        }
    }

    /**
     * Ping liviano para verificar que la conexion sigue activa
     */
    public static function ping()
    {
        if (self::$instance === null) return false;
        try {
            self::$instance->query('SELECT 1');
            return true;
        } catch (PDOException $e) {
            return false;
        }
    }

    /**
     * Test de conexion sin lanzar excepcion
     */
    public static function test($dbConfig)
    {
        try {
            self::reset();
            self::connect($dbConfig);
            self::reset();
            return array('ok' => true, 'error' => '');
        } catch (Exception $e) {
            return array('ok' => false, 'error' => $e->getMessage());
        }
    }

    public static function reset()
    {
        self::$instance = null;
    }

    // ── Internals ─────────────────────────────────────────────────────────────

    private static function createConnection($dbConfig)
    {
        $host = isset($dbConfig['host'])     ? $dbConfig['host']     : 'localhost';
        $port = isset($dbConfig['port'])     ? $dbConfig['port']     : '3306';
        $name = isset($dbConfig['name'])     ? $dbConfig['name']     : '';
        $user = isset($dbConfig['user'])     ? $dbConfig['user']     : 'root';
        $pass = isset($dbConfig['password']) ? $dbConfig['password'] : '';

        if (empty($name)) {
            throw new RuntimeException(
                "Nombre de base de datos no configurado en config.txt [db] name="
            );
        }

        $options = array(
            PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_EMULATE_PREPARES   => false,
            PDO::ATTR_PERSISTENT         => false,
        );

        // Intentar utf8mb4, fallback a utf8
        $charsets = array('utf8mb4', 'utf8');
        $lastError = '';

        foreach ($charsets as $charset) {
            $dsn     = "mysql:host={$host};port={$port};dbname={$name};charset={$charset}";
            $initCmd = ($charset === 'utf8mb4')
                ? "SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci"
                : "SET NAMES utf8";

            $options[PDO::MYSQL_ATTR_INIT_COMMAND] = $initCmd;

            try {
                $pdo = new PDO($dsn, $user, $pass, $options);

                // Configuraciones de sesion MySQL
                // Evitar cierre de conexion por inactividad
                try { $pdo->exec("SET SESSION wait_timeout        = 28800"); } catch (PDOException $e) {}
                try { $pdo->exec("SET SESSION interactive_timeout = 28800"); } catch (PDOException $e) {}
                // Lock wait base (el merge lo sube temporalmente)
                try { $pdo->exec("SET SESSION innodb_lock_wait_timeout = 50"); } catch (PDOException $e) {}

                self::$instance = $pdo;
                return $pdo;

            } catch (PDOException $e) {
                $lastError = $e->getMessage();
                // Si es error de charset intentar el siguiente
                if ($charset !== end($charsets)) continue;
            }
        }

        throw new RuntimeException(
            "No se pudo conectar a MySQL: " . $lastError
            . " — Verificar credenciales en config.txt"
        );
    }
}
