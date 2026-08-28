<?php
/**
 * Config.php
 * Parser manual completo de config.txt.
 * NO usa parse_ini_file en absoluto — evita problemas con
 * caracteres especiales ( ) { } [ ] en valores JSON.
 * Compatible con PHP 5.5+
 */

class Config
{
    private $values  = array();  // claves raiz: key => value
    private $sections = array(); // secciones: section => [key => value]
    private $vistas  = array();  // array parseado de vistas

    public function __construct($configPath)
    {
        if (!file_exists($configPath)) {
            throw new RuntimeException(
                "Archivo de configuracion no encontrado: " . $configPath
            );
        }

        $raw = file_get_contents($configPath);
        if ($raw === false) {
            throw new RuntimeException("No se pudo leer: " . $configPath);
        }

        $this->parse($raw);

        // Leer vistas desde vistas.txt si existe (tiene prioridad sobre config.txt)
        $vistasPath = dirname($configPath) . '/vistas.txt';
        if (file_exists($vistasPath)) {
            $rawVistas = file_get_contents($vistasPath);
            if ($rawVistas !== false) {
                $this->loadVistasTxt($rawVistas);
            }
        }
    }

    /**
     * Carga el JSON de vistas desde vistas.txt
     * Formato esperado:
     *   [vistas]
     *   json = [{"nombre":...}]
     */
    private function loadVistasTxt($raw)
    {
        $json = $this->extractVistaJson($raw);
        if (!empty($json)) {
            $this->parseVistaJson($json);
        }
    }

    // ── Getters ───────────────────────────────────────────────────────────────

    public function get($key, $default = null, $section = '')
    {
        if ($section !== '') {
            return isset($this->sections[$section][$key])
                ? $this->sections[$section][$key]
                : $default;
        }
        return isset($this->values[$key]) ? $this->values[$key] : $default;
    }

    public function section($section)
    {
        return isset($this->sections[$section]) ? $this->sections[$section] : array();
    }

    public function getVistas()
    {
        return $this->vistas;
    }

    public function getUrlBase()
    {
        $url = $this->get('url_base');
        if (empty($url)) {
            throw new RuntimeException("url_base no configurada en config.txt");
        }
        return rtrim($url, '/');
    }

    public function getApiKey()
    {
        $key = $this->get('api_key');
        if (empty($key)) {
            throw new RuntimeException("api_key no configurada en config.txt");
        }
        return $key;
    }

    public function getPageSize()
    {
        return (int)$this->get('page_size', 500);
    }

    /**
     * VENTANA MOVIL — dias hacia atras que se vuelven a leer en cada sync.
     *
     * El backend de Ekaru solo acepta un cursor por ID (`idpk > lastId`): no
     * tiene ningun filtro por fecha. Con el cursor parado en el ultimo ID
     * sincronizado, dos cosas nunca vuelven a llegar:
     *
     *   1. Una correccion sobre un comprobante ya sincronizado. Su ID no
     *      cambia, asi que queda para siempre debajo del cursor.
     *   2. Un comprobante cuyo ID se asigno ANTES de la sincronizacion pero
     *      que se cerro DESPUES (mesa abierta, pedido pendiente). Nace por
     *      debajo del cursor y no se lo vuelve a mirar nunca.
     *
     * El segundo caso es el que costo un sabado entero de tres sucursales.
     *
     * La solucion sin tocar el backend: no parar el cursor en el ultimo ID,
     * sino retrocederlo hasta el primer ID de los ultimos N dias. Releer esos
     * registros es inofensivo porque el merge ya es un upsert por clave
     * primaria (SchemaManager::mergeUpsert): lo que existe se actualiza, no
     * se duplica. Y de paso, esa actualizacion es la que trae las
     * correcciones del caso 1.
     *
     * 0 desactiva la ventana y deja el comportamiento anterior.
     */
    public function getResyncDias()
    {
        return (int)$this->get('resync_dias', 7);
    }

    /**
     * Respaldo para tablas SIN ninguna columna de fecha usable: cuantos IDs
     * retroceder a ciegas. Se usa solo si la ventana por fecha no se pudo
     * calcular. 0 = sin respaldo.
     */
    public function getResyncMargenIds()
    {
        return (int)$this->get('resync_margen_ids', 0);
    }

    public function getDbConfig()
    {
        $db = $this->section('db');
        return array(
            'host'     => isset($db['host'])     ? $db['host']     : 'localhost',
            'port'     => isset($db['port'])     ? $db['port']     : '3306',
            'name'     => isset($db['name'])     ? $db['name']     : '',
            'user'     => isset($db['user'])     ? $db['user']     : 'root',
            'password' => isset($db['password']) ? $db['password'] : '',
        );
    }

    public function getHttpTimeout()
    {
        return (int)$this->get('http_timeout', 60);
    }

    public function getHttpRetries()
    {
        return (int)$this->get('http_retries', 3);
    }

    public function getHttpRetryDelay()
    {
        return (int)$this->get('http_retry_delay', 2);
    }

    public function getWorkerTimeout()
    {
        return (int)$this->get('worker_timeout_minutes', 120);
    }

    public function getVersion()
    {
        return $this->get('version', '1.0.0');
    }

    public function getAppName()
    {
        return $this->get('app_name', 'Ekaru BI Sync');
    }

    /**
     * URL del webservice de vistas del backend
     * Ejemplo: http://micliente.ekaru.com/ekaru/bi_con/vistas_bi_ws.php?apikey=xxx
     */
    public function getVistasWsUrl()
    {
        return $this->getUrlBase() . '/bi_con/vistas_bi_ws.php?apikey=' . $this->getApiKey();
    }

    public function validate()
    {
        $errors = array();

        if (empty($this->get('url_base'))) {
            $errors[] = "url_base no configurada";
        }
        if (empty($this->get('api_key'))) {
            $errors[] = "api_key no configurada";
        }

        $db = $this->getDbConfig();
        if (empty($db['name'])) {
            $errors[] = "Nombre de base de datos no configurado ([db] name=)";
        }

        if (empty($this->vistas)) {
            $errors[] = "No hay vistas configuradas — descargar vistas.txt del ERP y copiarlo junto a config.txt";
        }

        return $errors;
    }

    // ── Parser manual ─────────────────────────────────────────────────────────

    /**
     * Parsea el archivo completo linea por linea.
     * Maneja:
     *   - Comentarios (; y #)
     *   - Secciones [nombre]
     *   - Claves simples: key = value
     *   - Seccion especial [vistas] con json= que puede contener
     *     cualquier caracter especial
     */
    private function parse($raw)
    {
        // Normalizar saltos de linea
        $raw   = str_replace("\r\n", "\n", $raw);
        $raw   = str_replace("\r",   "\n", $raw);
        $lines = explode("\n", $raw);

        $currentSection = '';
        $inVistas       = false;
        $jsonValue      = '';
        $jsonStarted    = false;
        $bracketDepth   = 0;

        foreach ($lines as $line) {
            // ── Modo especial: acumulando JSON multilínea ─────────────────────
            if ($inVistas && $jsonStarted && $bracketDepth > 0) {
                $trimmed = trim($line);

                // Ignorar comentarios dentro del bloque
                if (!empty($trimmed) && $trimmed[0] !== ';' && $trimmed[0] !== '#') {
                    $jsonValue    .= $trimmed;
                    $bracketDepth += substr_count($trimmed, '[') - substr_count($trimmed, ']');

                    if ($bracketDepth <= 0) {
                        $jsonStarted = false;
                        $this->parseVistaJson($jsonValue);
                    }
                }
                continue;
            }

            $trimmed = trim($line);

            // Ignorar vacias y comentarios
            if (empty($trimmed) || $trimmed[0] === ';' || $trimmed[0] === '#') {
                continue;
            }

            // ── Deteccion de seccion ──────────────────────────────────────────
            if (preg_match('/^\[([^\]]+)\]/', $trimmed, $m)) {
                $currentSection = strtolower(trim($m[1]));
                $inVistas       = ($currentSection === 'vistas');

                if (!isset($this->sections[$currentSection])) {
                    $this->sections[$currentSection] = array();
                }
                continue;
            }

            // ── Clave = valor ─────────────────────────────────────────────────
            // Buscar el primer = en la linea
            $eqPos = strpos($trimmed, '=');
            if ($eqPos === false) continue;

            $key   = trim(substr($trimmed, 0, $eqPos));
            $value = trim(substr($trimmed, $eqPos + 1));

            // Validar clave (solo alfanumericos y _)
            if (!preg_match('/^[a-zA-Z0-9_]+$/', $key)) continue;

            // ── Seccion [vistas]: extraer JSON sin tocar el valor ─────────────
            if ($inVistas && strtolower($key) === 'json') {
                // Quitar comillas envolventes si las tiene
                $json = $this->stripQuotes($value);

                // Caso A: JSON completo en una linea
                if (!empty($json) && $json[0] === '[') {
                    $depth = substr_count($json, '[') - substr_count($json, ']');
                    if ($depth <= 0) {
                        // JSON completo
                        $this->parseVistaJson($json);
                    } else {
                        // JSON multilínea — seguir acumulando
                        $jsonValue    = $json;
                        $bracketDepth = $depth;
                        $jsonStarted  = true;
                    }
                } elseif (empty($json)) {
                    // json = (vacio, el array empieza en la siguiente linea)
                    $jsonValue    = '';
                    $bracketDepth = 0;
                    $jsonStarted  = true;
                } else {
                    // Valor sin corchete — intentar parsear igual
                    $this->parseVistaJson($json);
                }
                continue;
            }

            // ── Claves normales ───────────────────────────────────────────────
            // Quitar comillas envolventes del valor
            $value = $this->stripQuotes($value);

            if ($currentSection !== '' && $currentSection !== 'vistas') {
                $this->sections[$currentSection][$key] = $value;
            } elseif ($currentSection === '') {
                $this->values[$key] = $value;
            }
        }
    }

    /**
     * Parsea el string JSON de vistas y lo guarda en $this->vistas
     */
    private function parseVistaJson($json)
    {
        $json = trim($json);
        if (empty($json)) return;

        $decoded = json_decode($json, true);

        if (json_last_error() !== JSON_ERROR_NONE) {
            throw new RuntimeException(
                "JSON de vistas invalido: " . json_last_error_msg()
                . " — Revisar el valor de json= en [vistas]"
            );
        }

        if (is_array($decoded)) {
            $this->vistas = $decoded;
        }
    }

    /**
     * Quita comillas simples o dobles envolventes de un string
     */
    private function stripQuotes($value)
    {
        $value = trim($value);
        if (strlen($value) < 2) return $value;

        $first = $value[0];
        $last  = $value[strlen($value) - 1];

        if (($first === "'" && $last === "'") ||
            ($first === '"' && $last === '"')) {
            return substr($value, 1, -1);
        }

        return $value;
    }

    /**
     * Extrae el valor de json= dentro de la seccion [vistas].
     * Soporta una linea, con comillas, o multilínea.
     */
    private function extractVistaJson($raw)
    {
        $lines        = explode("\n", str_replace("\r\n", "\n", $raw));
        $inVistas     = false;
        $inJson       = false;
        $jsonLines    = array();
        $bracketDepth = 0;

        foreach ($lines as $line) {
            $trimmed = trim($line);

            if (preg_match('/^\[vistas\]/i', $trimmed)) {
                $inVistas = true;
                continue;
            }

            if ($inVistas && preg_match('/^\[[a-zA-Z]/', $trimmed)) {
                break;
            }

            if (!$inVistas) continue;

            if (empty($trimmed) || $trimmed[0] === ';' || $trimmed[0] === '#') {
                continue;
            }

            // Acumular JSON multilínea
            if ($inJson) {
                $jsonLines[]   = $trimmed;
                $bracketDepth += substr_count($trimmed, '[') - substr_count($trimmed, ']');
                if ($bracketDepth <= 0) break;
                continue;
            }

            // Detectar json=
            if (preg_match('/^json\s*=\s*(.*)/is', $trimmed, $m)) {
                $value = trim($m[1]);

                // Con comillas envolventes
                if ((substr($value, 0, 1) === "'" && substr($value, -1) === "'") ||
                    (substr($value, 0, 1) === '"' && substr($value, -1) === '"')) {
                    return substr($value, 1, -1);
                }

                // JSON completo en una linea
                if (substr($value, 0, 1) === '[') {
                    $depth = substr_count($value, '[') - substr_count($value, ']');
                    if ($depth <= 0) return $value;
                    $jsonLines[]  = $value;
                    $bracketDepth = $depth;
                    $inJson       = true;
                    continue;
                }

                // json= vacio, array en linea siguiente
                if (empty($value)) {
                    $inJson = true;
                }
            }
        }

        return !empty($jsonLines) ? implode('', $jsonLines) : '';
    }
}
