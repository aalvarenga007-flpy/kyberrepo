<?php
// Loaded only by the dedicated HTTPS gateway. Fail closed outside that gateway.
function panel_fail($message, $code = 403) {
    http_response_code($code);
    header('Content-Type: text/html; charset=utf-8');
    echo '<!doctype html><html lang="es"><meta charset="utf-8"><title>Panel de sincronización</title>';
    echo '<body style="font:18px system-ui;max-width:650px;margin:80px auto;padding:24px">';
    echo '<h1>Panel de sincronización</h1><p>' . htmlspecialchars($message, ENT_QUOTES, 'UTF-8') . '</p>';
    echo '<p><a href="/">Volver a Kyber</a></p></body></html>';
    exit;
}

function panel_authorize_user($uid, $company) {
    // Independent of the bearer ticket: revocations/role changes take effect on
    // the next request. Never return password hashes or connection exceptions.
    try {
        $database = getenv('AUTH_DATABASE');
        if ($database !== 'conepasa_auth_pruebas') return false;
        $pdo = new PDO('mysql:host=127.0.0.1;port=3306;dbname=' . $database . ';charset=utf8mb4',
            getenv('AUTH_MYSQL_USER'), getenv('AUTH_MYSQL_PASSWORD'),
            array(PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_TIMEOUT => 5));
        $q = $pdo->prepare('SELECT rol, activo, empresas, debe_cambiar_password FROM usuarios WHERE id = ?');
        $q->execute(array($uid));
        $u = $q->fetch(PDO::FETCH_ASSOC);
        if (!$u || $u['rol'] !== 'admin' || !(int)$u['activo'] || (int)$u['debe_cambiar_password']) return false;
        $companies = array_filter(array_map('trim', explode(',', (string)$u['empresas'])));
        return !$companies || in_array($company, $companies, true);
    } catch (Throwable $e) {
        return false;
    }
}

function panel_lease_valid($record, $root) {
    if (!is_array($record) || !isset($record['lease'], $record['uid'], $record['session_expires'])
        || !preg_match('/^[a-f0-9]{64}$/D', $record['lease']) || $record['session_expires'] <= time()) return false;
    $path = $root . '/leases/' . $record['lease'];
    $lease = is_file($path) ? json_decode(file_get_contents($path), true) : null;
    return is_array($lease) && (int)$lease['uid'] === (int)$record['uid'] && $lease['expires'] > time();
}

function panel_consume_ticket($token, $company, $root) {
    if (!is_string($token) || !preg_match('/^[a-f0-9]{64}$/D', $token)) return null;
    $path = $root . '/tickets/' . hash('sha256', $token);
    $file = @fopen($path, 'r');
    if (!$file) return null;
    if (!flock($file, LOCK_EX)) { fclose($file); return null; }
    // Unlink success is required: parallel use of the same ticket loses.
    if (!@unlink($path)) { fclose($file); return null; }
    $ticket = json_decode(stream_get_contents($file), true);
    fclose($file);
    if (!is_array($ticket) || ($ticket['company'] ?? '') !== $company
        || ($ticket['expires'] ?? 0) <= time() || !panel_lease_valid($ticket, $root)) return null;
    return $ticket;
}

function panel_csrf_valid($method, $received, $expected, $origin) {
    return $method === 'POST' && is_string($received) && strlen($expected) === 64
        && hash_equals($expected, $received) && $origin === getenv('KYBER_PANEL_ORIGIN');
}

function panel_redact($value) {
    global $config;
    if (is_array($value)) {
        $clean = array();
        foreach ($value as $key => $item) {
            if (preg_match('/password|api_key|endpoint_url/i', (string)$key)) continue;
            $clean[$key] = panel_redact($item);
        }
        return $clean;
    }
    if (!is_string($value)) return $value;
    if (isset($config)) {
        $db = $config->getDbConfig();
        foreach (array($config->getApiKey(), $db['password'] ?? '') as $secret) {
            if (is_string($secret) && strlen($secret) >= 4) $value = str_replace($secret, '[oculto]', $value);
        }
    }
    return preg_replace('/([?&](?:api_key|token|password)=)[^&\s"<>]+/i', '$1[oculto]', $value);
}

function panel_wake_worker() {
    $company = $_SERVER['KYBER_PANEL_COMPANY'];
    $path = getenv('KYBER_PANEL_STATE') . '/requests/' . $company;
    if (file_put_contents($path, (string)microtime(true), LOCK_EX) === false) return false;
    return true;
}
