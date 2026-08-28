<?php
/**
 * HttpClient.php
 * Cliente HTTP robusto con CURL, retry automatico y compresion gzip.
 * Compatible con PHP 5.5+
 */

class HttpClient
{
    private $apiKey;
    private $timeout;
    private $maxRetries;
    private $retryDelay;
    private $logger;

    public function __construct($apiKey, $logger, $timeout = 60, $maxRetries = 3, $retryDelay = 2)
    {
        $this->apiKey     = $apiKey;
        $this->logger     = $logger;
        $this->timeout    = $timeout;
        $this->maxRetries = $maxRetries;
        $this->retryDelay = $retryDelay;
    }

    /**
     * Descarga una pagina de datos del backend.
     *
     * @param  string   $endpointUrl  URL completa del endpoint
     * @param  int      $page         Numero de pagina (1-based)
     * @param  int      $pageSize     Registros por pagina
     * @param  int|null $lastId       ID incremental (null = sin filtro)
     * @param  string   $idpkParam    Nombre del parametro GET del ID
     * @return array    Estructura del JSON del backend
     */
    public function fetchPage($endpointUrl, $page, $pageSize, $lastId = null, $idpkParam = '', $extraParams = array())
    {
        $params = array(
            'apikey'   => $this->apiKey,
            'page'     => $page,
            'pagesize' => $pageSize,
        );

        if ($lastId !== null && $idpkParam !== '') {
            $params[$idpkParam] = $lastId;
        }

        // Parametros extra (ej: test_con=s)
        if (!empty($extraParams)) {
            $params = array_merge($params, $extraParams);
        }

        $url = $endpointUrl . '?' . http_build_query($params);

        $this->logger->debug("HTTP GET: " . $url);

        $attempt   = 0;
        $lastError = '';

        while ($attempt < $this->maxRetries) {
            $attempt++;

            try {
                $response = $this->curlGet($url);
                return $this->parseResponse($response, $url);

            } catch (HttpRetryableException $e) {
                $lastError = $e->getMessage();
                $this->logger->warning("Intento {$attempt}/{$this->maxRetries} fallo: " . $lastError);

                if ($attempt < $this->maxRetries) {
                    $wait = $this->retryDelay * $attempt;
                    $this->logger->info("Reintentando en {$wait}s...");
                    sleep($wait);
                }

            } catch (HttpFatalException $e) {
                // Preservar el tipo — ETLRunner distingue fatal (drop staging)
                // de retryable (pausar conservando staging). Convertir a
                // RuntimeException haria que ambos catch de ETLRunner sean
                // codigo muerto y todo terminara como error + reinicio.
                throw $e;
            }
        }

        // Reintentos agotados sobre un error retryable: propagar como retryable
        // para que ETLRunner pause conservando la staging (retoma exacta).
        throw new HttpRetryableException(
            "Fallo despues de {$this->maxRetries} intentos. Ultimo error: " . $lastError
        );
    }

    /**
     * Test rapido de conexion — usa page=2 para no consumir
     * el limite de page=1 (el backend permite hasta 5 por hora)
     */
    /**
     * Test rapido de conexion — usa page=2 y test_con=s para no consumir
     * el limite de page=1 (el backend permite hasta 5 por hora).
     * El parametro test_con=s le avisa al backend que es una prueba.
     */
    public function testConnection($endpointUrl)
    {
        try {
            $result = $this->fetchPage($endpointUrl, 2, 1, null, '', array('test_con' => 's'));
            return array('ok' => true, 'error' => '', 'data' => $result);
        } catch (Exception $e) {
            return array('ok' => false, 'error' => $e->getMessage(), 'data' => array());
        }
    }

    // ── CURL ──────────────────────────────────────────────────────────────────

    private function curlGet($url)
    {
        $ch = curl_init();

        curl_setopt_array($ch, array(
            CURLOPT_URL            => $url,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_FOLLOWLOCATION => true,
            CURLOPT_MAXREDIRS      => 3,
            CURLOPT_TIMEOUT        => $this->timeout,
            CURLOPT_CONNECTTIMEOUT => 15,
            CURLOPT_ENCODING       => 'gzip, deflate',
            CURLOPT_USERAGENT      => 'EkaruBI-ETL/1.0',
            CURLOPT_HTTPHEADER     => array(
                'Accept: application/json',
                'Accept-Encoding: gzip, deflate',
            ),
            CURLOPT_SSL_VERIFYPEER => false,
            CURLOPT_SSL_VERIFYHOST => false,
        ));

        $body     = curl_exec($ch);
        $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $curlErr  = curl_errno($ch);
        $curlMsg  = curl_error($ch);
        $info     = curl_getinfo($ch);

        curl_close($ch);

        if ($body === false || $curlErr !== 0) {
            // CUALQUIER error de transporte CURL es de red (timeout, connection
            // reset [35/56], SSL, DNS, got-nothing, etc.) => SIEMPRE retryable.
            // Nunca fatal: un sync no debe reiniciar de cero por un corte de
            // conexion. HttpRetryableException -> ETLRunner pausa conservando la
            // staging y el worker retoma desde donde quedo.
            throw new HttpRetryableException("CURL error [{$curlErr}]: " . $curlMsg);
        }

        $elapsed = isset($info['total_time']) ? $info['total_time'] : 0;
        $this->logger->debug("HTTP {$httpCode} — {$elapsed}s — " . strlen($body) . " bytes");

        return array(
            'code' => $httpCode,
            'body' => $body,
            'info' => $info,
        );
    }

    // ── Parseo de respuesta ───────────────────────────────────────────────────

    private function parseResponse($response, $url)
    {
        $code = $response['code'];
        $body = $response['body'];

        // Errores HTTP reintentables
        if (in_array($code, array(429, 502, 503, 504))) {
            throw new HttpRetryableException("HTTP {$code} en " . $url);
        }

        // Errores fatales
        if ($code === 401 || $code === 403) {
            throw new HttpFatalException("HTTP {$code} — API Key invalida o sin acceso: " . $url);
        }

        if ($code === 404) {
            throw new HttpFatalException("HTTP 404 — Endpoint no encontrado: " . $url);
        }

        if ($code < 200 || $code >= 300) {
            throw new HttpRetryableException("HTTP {$code} inesperado en " . $url);
        }

        if (empty(trim($body))) {
            throw new HttpRetryableException("Respuesta vacia del servidor: " . $url);
        }

        // Detectar errores SQL o PHP devueltos como texto plano (no JSON)
        // El backend a veces devuelve errores de MariaDB/MySQL directamente
        if (substr(trim($body), 0, 1) !== '{' && substr(trim($body), 0, 1) !== '[') {
            $preview = substr(trim($body), 0, 300);

            // Timeout de consulta (max_statement_time) — TRANSITORIO.
            // El backend aborta la query por proteccion y devuelve el error en
            // texto plano. Es retryable: pausar conservando staging y retomar
            // desde la misma pagina cuando baje la carga del servidor.
            if (stripos($body, 'max_statement_time') !== false ||
                stripos($body, 'Query execution was interrupted') !== false ||
                stripos($body, 'statement timeout') !== false) {
                throw new HttpRetryableException(
                    "Timeout de consulta en el backend (max_statement_time): " . $preview
                );
            }

            // Limite de consultas por HORA/DIA alcanzado — TRANSITORIO.
            // Se libera al pasar la hora/dia; pausar y retomar despues.
            if (stripos($body, 'Superaste la cantidad maxima') !== false) {
                throw new HttpRetryableException(
                    "Limite de consultas del backend alcanzado (se libera luego): " . $preview
                );
            }

            // Error SQL de MariaDB/MySQL
            if (stripos($body, 'you have an error in your sql') !== false ||
                stripos($body, 'mariadb') !== false ||
                stripos($body, 'mysql') !== false ||
                stripos($body, 'syntax error') !== false) {
                throw new HttpFatalException(
                    "Error SQL en el backend: " . $preview
                );
            }

            // Error PHP
            if (stripos($body, 'fatal error') !== false ||
                stripos($body, 'parse error') !== false ||
                stripos($body, 'warning:') !== false) {
                throw new HttpFatalException(
                    "Error PHP en el backend: " . $preview
                );
            }

            // Cualquier otra respuesta no-JSON
            throw new HttpFatalException(
                "Respuesta inesperada del servidor (no es JSON): " . $preview
            );
        }

        $json = json_decode($body, true);

        if (json_last_error() !== JSON_ERROR_NONE) {
            $preview = substr($body, 0, 200);
            throw new HttpFatalException(
                "JSON invalido: " . json_last_error_msg() . " — Preview: " . $preview
            );
        }

        if (!is_array($json)) {
            throw new HttpFatalException("La respuesta JSON no es un objeto: " . $url);
        }

        if (!array_key_exists('data', $json)) {
            throw new HttpFatalException("Respuesta JSON sin campo 'data': " . $url);
        }

        // Error del backend
        if (isset($json['schema']['errores'])) {
            $errorMsg = isset($json['data'][0]['errores']) ? $json['data'][0]['errores'] : 'Error desconocido del backend';

            // Pausa temporal por horario de alta carga — TRANSITORIO.
            // El backend rechaza consultas en franjas pico; retomar mas tarde.
            if (stripos($errorMsg, 'Pausado') !== false ||
                stripos($errorMsg, 'mucha carga') !== false ||
                stripos($errorMsg, 'estabilidad del servidor') !== false) {
                throw new HttpRetryableException("Backend en pausa temporal (horario de carga): " . $errorMsg);
            }

            // Resto (credenciales, licencia, BI inactivo) — FATAL.
            throw new HttpFatalException("Error del backend: " . $errorMsg);
        }

        return $json;
    }
}

// ── Excepciones ───────────────────────────────────────────────────────────────

class HttpRetryableException extends RuntimeException {}
class HttpFatalException extends RuntimeException {}
