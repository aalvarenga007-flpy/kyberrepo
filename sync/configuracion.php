<?php
/**
 * configuracion.php
 * Pantalla de configuracion inicial e instalacion.
 * Se muestra automaticamente cuando config.txt tiene valores CONFIGURAR.
 * Tambien accesible desde el panel en cualquier momento.
 * Compatible con PHP 5.5+
 */

define('BI_ROOT', __DIR__);

require_once BI_ROOT . '/src/Config.php';
require_once BI_ROOT . '/src/Database.php';

$configPath = BI_ROOT . '/config.txt';
$error      = '';
$success    = '';
$testResults = array();

// ── Valores actuales del config ───────────────────────────────────────────────
$current = array(
    'url_base'  => '',
    'api_key'   => '',
    'db_host'   => 'localhost',
    'db_port'   => '3306',
    'db_name'   => 'ekaru_bi',
    'db_user'   => 'root',
    'db_pass'   => '',
);

if (file_exists($configPath)) {
    try {
        $cfg = new Config($configPath);
        $db  = $cfg->getDbConfig();

        $urlBase = $cfg->get('url_base');
        $apiKey  = $cfg->get('api_key');

        $current['url_base'] = ($urlBase === 'CONFIGURAR') ? '' : $urlBase;
        $current['api_key']  = ($apiKey  === 'CONFIGURAR') ? '' : $apiKey;
        $current['db_host']  = $db['host'];
        $current['db_port']  = $db['port'];
        $current['db_name']  = $db['name'];
        $current['db_user']  = $db['user'];
        $current['db_pass']  = $db['password'];
    } catch (Exception $e) {
        // Config invalido — mostrar formulario vacio
    }
}

// ── Procesar formulario ───────────────────────────────────────────────────────
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $action = isset($_POST['action']) ? $_POST['action'] : '';

    $urlBase = trim(isset($_POST['url_base']) ? $_POST['url_base'] : '');
    $apiKey  = trim(isset($_POST['api_key'])  ? $_POST['api_key']  : '');
    $dbHost  = trim(isset($_POST['db_host'])  ? $_POST['db_host']  : 'localhost');
    $dbPort  = trim(isset($_POST['db_port'])  ? $_POST['db_port']  : '3306');
    $dbName  = trim(isset($_POST['db_name'])  ? $_POST['db_name']  : 'ekaru_bi');
    $dbUser  = trim(isset($_POST['db_user'])  ? $_POST['db_user']  : 'root');
    $dbPass  = isset($_POST['db_pass'])       ? $_POST['db_pass']  : '';

    // Actualizar valores actuales para repoblar el formulario
    $current = array(
        'url_base' => $urlBase,
        'api_key'  => $apiKey,
        'db_host'  => $dbHost,
        'db_port'  => $dbPort,
        'db_name'  => $dbName,
        'db_user'  => $dbUser,
        'db_pass'  => $dbPass,
    );

    // ── Accion: probar conexiones ─────────────────────────────────────────────
    if ($action === 'test') {
        // Test MySQL
        $testResults['mysql'] = testMySQL($dbHost, $dbPort, $dbName, $dbUser, $dbPass);

        // Test Backend
        if (!empty($urlBase) && !empty($apiKey)) {
            $testResults['backend'] = testBackend($urlBase, $apiKey);
        } else {
            $testResults['backend'] = array(
                'ok'      => false,
                'message' => 'Completar URL Base y API Key para probar',
            );
        }
    }

    // ── Accion: guardar ───────────────────────────────────────────────────────
    if ($action === 'save') {
        $errors = array();

        if (empty($urlBase)) $errors[] = "URL Base es requerida";
        if (empty($apiKey))  $errors[] = "API Key es requerida";
        if (empty($dbName))  $errors[] = "Nombre de base de datos es requerido";

        if (empty($errors)) {
            // Probar conexiones antes de guardar
            $mysqlTest   = testMySQL($dbHost, $dbPort, $dbName, $dbUser, $dbPass);
            $backendTest = testBackend($urlBase, $apiKey);

            if (!$mysqlTest['ok']) {
                $error = "No se pudo conectar a MySQL: " . $mysqlTest['message'];
            } elseif (!$backendTest['ok']) {
                $error = "No se pudo conectar al backend: " . $backendTest['message'];
            } else {
                // Guardar config.txt
                $saved = guardarConfig($configPath, $urlBase, $apiKey, $dbHost, $dbPort, $dbName, $dbUser, $dbPass);

                if ($saved !== true) {
                    $error = $saved;
                } else {
                    // Descargar vistas.txt automaticamente
                    $vistasResult = descargarYGuardarVistas($urlBase, $apiKey, BI_ROOT);

                    if ($vistasResult['ok']) {
                        // Todo OK — redirigir al panel
                        header('Location: index.php?configurado=1');
                        exit;
                    } else {
                        // Config guardado pero vistas fallaron — igual ir al panel
                        header('Location: index.php?configurado=1&vistas_error=' . urlencode($vistasResult['error']));
                        exit;
                    }
                }
            }
        } else {
            $error = implode(', ', $errors);
        }
    }
}

// ── Detectar si es primera instalacion ───────────────────────────────────────
$esPrimeraInstalacion = (empty($current['url_base']) && empty($current['api_key']));

// ── Helpers ───────────────────────────────────────────────────────────────────

function testMySQL($host, $port, $name, $user, $pass)
{
    try {
        // Si no hay nombre de BD, intentar conectar sin seleccionar BD
        if (empty($name)) {
            $dsn = "mysql:host={$host};port={$port};charset=utf8";
        } else {
            $dsn = "mysql:host={$host};port={$port};dbname={$name};charset=utf8";
        }

        $pdo = new PDO($dsn, $user, $pass, array(
            PDO::ATTR_ERRMODE    => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_TIMEOUT    => 5,
        ));

        // Si la BD no existe, intentar crearla
        if (!empty($name)) {
            $pdo->exec(
                "CREATE DATABASE IF NOT EXISTS `{$name}`
                 CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            );
        }

        return array('ok' => true, 'message' => 'Conexion exitosa' . (!empty($name) ? " — base de datos lista" : ''));

    } catch (PDOException $e) {
        $msg = $e->getMessage();
        // Simplificar mensaje de error
        if (strpos($msg, 'Connection refused') !== false || strpos($msg, 'connect') !== false) {
            $msg = "No se puede conectar a MySQL en {$host}:{$port} — verificar que WAMP este corriendo";
        } elseif (strpos($msg, 'Access denied') !== false) {
            $msg = "Acceso denegado — verificar usuario y contraseña";
        }
        return array('ok' => false, 'message' => $msg);
    }
}

function testBackend($urlBase, $apiKey)
{
    $urlBase = rtrim($urlBase, '/');
    $url     = $urlBase . '/bi_con/vistas_bi_ws.php?apikey=' . $apiKey . '&test_con=s';

    $ch = curl_init();
    curl_setopt_array($ch, array(
        CURLOPT_URL            => $url,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT        => 10,
        CURLOPT_CONNECTTIMEOUT => 5,
        CURLOPT_SSL_VERIFYPEER => false,
        CURLOPT_SSL_VERIFYHOST => false,
        CURLOPT_USERAGENT      => 'EkaruBI-ETL/1.0',
    ));

    $body     = curl_exec($ch);
    $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $curlErr  = curl_errno($ch);
    $curlMsg  = curl_error($ch);
    curl_close($ch);

    if ($curlErr !== 0) {
        return array('ok' => false, 'message' => "Error de red: " . $curlMsg);
    }

    if ($httpCode === 401 || $httpCode === 403) {
        return array('ok' => false, 'message' => "API Key invalida (HTTP {$httpCode})");
    }

    if ($httpCode === 404) {
        return array('ok' => false, 'message' => "URL no encontrada — verificar URL Base");
    }

    if ($httpCode < 200 || $httpCode >= 300) {
        return array('ok' => false, 'message' => "Error HTTP {$httpCode}");
    }

    $json = json_decode($body, true);
    if (json_last_error() !== JSON_ERROR_NONE || !is_array($json)) {
        $preview = substr(trim($body), 0, 100);
        return array('ok' => false, 'message' => "Respuesta invalida del servidor: " . $preview);
    }

    $count = count($json);
    return array('ok' => true, 'message' => "Conexion exitosa — {$count} vistas disponibles");
}

function guardarConfig($path, $urlBase, $apiKey, $dbHost, $dbPort, $dbName, $dbUser, $dbPass)
{
    $urlBase = rtrim($urlBase, '/');
    $content = "; ============================================================\n"
             . "; Ekaru BI Sync — Archivo de Configuracion\n"
             . "; Generado: " . date('Y-m-d H:i:s') . "\n"
             . "; ============================================================\n"
             . "app_name = Ekaru BI Sync\n"
             . "version  = 1.0.0\n"
             . "; URL base del backend SaaS del cliente (sin slash final)\n"
             . "url_base = " . $urlBase . "\n"
             . "; API Key del cliente\n"
             . "api_key  = " . $apiKey . "\n"
             . "; Registros por pagina (recomendado 500-1000, maximo real backend: 10000)\n"
             . "page_size = 1000\n"
             . "; Minutos antes de considerar un worker colgado\n"
             . "worker_timeout_minutes = 120\n"
             . "; Horas sin sincronizar para encolar automaticamente (0 = desactivado)\n"
             . "auto_sync_hours = 24\n"
             . "; ── Base de datos MySQL local ────────────────────────────────\n"
             . "[db]\n"
             . "host     = " . $dbHost . "\n"
             . "port     = " . $dbPort . "\n"
             . "name     = " . $dbName . "\n"
             . "user     = " . $dbUser . "\n"
             . "password = " . $dbPass . "\n"
             . "; ── HTTP ─────────────────────────────────────────────────────\n"
             . "http_timeout     = 60\n"
             . "http_retries     = 3\n"
             . "http_retry_delay = 2\n";

    if (file_put_contents($path, $content) === false) {
        return "No se pudo escribir config.txt — verificar permisos en " . dirname($path);
    }

    return true;
}

function descargarYGuardarVistas($urlBase, $apiKey, $biRoot)
{
    $urlBase = rtrim($urlBase, '/');
    $url     = $urlBase . '/bi_con/vistas_bi_ws.php?apikey=' . $apiKey;

    $ch = curl_init();
    curl_setopt_array($ch, array(
        CURLOPT_URL            => $url,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT        => 15,
        CURLOPT_CONNECTTIMEOUT => 5,
        CURLOPT_SSL_VERIFYPEER => false,
        CURLOPT_SSL_VERIFYHOST => false,
    ));

    $body     = curl_exec($ch);
    $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);

    if (!$body || $httpCode < 200 || $httpCode >= 300) {
        return array('ok' => false, 'error' => "No se pudieron descargar las vistas (HTTP {$httpCode})");
    }

    $json = json_decode($body, true);
    if (json_last_error() !== JSON_ERROR_NONE || !is_array($json) || empty($json)) {
        return array('ok' => false, 'error' => "Respuesta invalida al descargar vistas");
    }

    $vistas = array();
    foreach ($json as $v) {
        if (!isset($v['nombre']) || !isset($v['endpoint'])) continue;
        $vistas[] = array(
            'nombre'   => $v['nombre'],
            'endpoint' => $v['endpoint'],
            'idpk'     => isset($v['idpk']) ? $v['idpk'] : '',
            'keyset'   => isset($v['keyset']) ? $v['keyset'] : false,
        );
    }

    $content = "; Ekaru BI Sync — Vistas\n"
             . "; Descargado: " . date('Y-m-d H:i:s') . "\n\n"
             . "[vistas]\n"
             . "json = " . json_encode($vistas, JSON_UNESCAPED_UNICODE) . "\n";

    file_put_contents($biRoot . '/vistas.txt', $content);

    return array('ok' => true, 'count' => count($vistas));
}

?>
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ekaru BI Sync — Configuración</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
    --bg:#0f1117;--bg2:#1a1d27;--bg3:#222535;--border:#2e3147;
    --accent:#4f7df3;--accent2:#6c8ff5;
    --green:#22c55e;--red:#ef4444;--yellow:#f59e0b;
    --text:#e2e8f0;--text2:#94a3b8;--text3:#64748b;
    --radius:10px;
}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}

.card{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);width:100%;max-width:560px;overflow:hidden;box-shadow:0 8px 40px rgba(0,0,0,.5)}

.card-header{padding:28px 32px 20px;border-bottom:1px solid var(--border)}
.card-logo{width:42px;height:42px;background:var(--accent);border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:20px;margin-bottom:14px}
.card-title{font-size:20px;font-weight:700}
.card-sub{font-size:13px;color:var(--text2);margin-top:4px}

.card-body{padding:28px 32px}

/* Form */
.form-group{margin-bottom:20px}
.form-label{display:block;font-size:12px;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:7px}
.form-input{width:100%;background:var(--bg3);border:1px solid var(--border);border-radius:7px;padding:10px 14px;color:var(--text);font-size:14px;outline:none;transition:border-color .15s}
.form-input:focus{border-color:var(--accent)}
.form-input::placeholder{color:var(--text3)}
.form-hint{font-size:11px;color:var(--text3);margin-top:5px}

.form-row{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.form-row-3{display:grid;grid-template-columns:2fr 1fr;gap:14px}

.section-title{font-size:13px;font-weight:700;color:var(--text2);text-transform:uppercase;letter-spacing:.5px;margin:24px 0 16px;padding-bottom:8px;border-bottom:1px solid var(--border)}

/* Buttons */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:10px 20px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;border:none;transition:all .15s;width:100%}
.btn:disabled{opacity:.4;cursor:not-allowed}
.btn-primary{background:var(--accent);color:#fff}.btn-primary:hover:not(:disabled){background:var(--accent2)}
.btn-ghost{background:transparent;border:1px solid var(--border);color:var(--text2)}.btn-ghost:hover:not(:disabled){background:var(--bg3)}
.btn-row{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:24px}

/* Alerts */
.alert{padding:12px 16px;border-radius:8px;font-size:13px;margin-bottom:20px;border:1px solid}
.alert-error{background:rgba(239,68,68,.1);border-color:rgba(239,68,68,.3);color:var(--red)}
.alert-success{background:rgba(34,197,94,.1);border-color:rgba(34,197,94,.3);color:var(--green)}

/* Test results */
.test-results{display:flex;flex-direction:column;gap:8px;margin-top:16px}
.test-item{display:flex;align-items:center;gap:10px;padding:10px 14px;border-radius:8px;background:var(--bg3);border:1px solid var(--border);font-size:13px}
.test-icon{font-size:16px;flex-shrink:0}
.test-label{font-weight:600;flex-shrink:0;width:80px}
.test-msg{color:var(--text2)}
.test-item.ok   .test-msg{color:var(--green)}
.test-item.fail .test-msg{color:var(--red)}

/* Back link */
.back-link{display:inline-flex;align-items:center;gap:6px;color:var(--text3);font-size:13px;text-decoration:none;margin-bottom:20px}
.back-link:hover{color:var(--text2)}

/* Loading */
.loading{display:none;text-align:center;padding:12px;color:var(--text2);font-size:13px}
</style>
</head>
<body>

<div style="width:100%;max-width:560px">

    <?php if (!$esPrimeraInstalacion): ?>
    <a href="index.php" class="back-link">← Volver al panel</a>
    <?php endif; ?>

    <div class="card">
        <div class="card-header">
            <div class="card-logo">⚙</div>
            <div class="card-title">
                <?php echo $esPrimeraInstalacion ? 'Instalación inicial' : 'Configuración'; ?>
            </div>
            <div class="card-sub">
                <?php echo $esPrimeraInstalacion
                    ? 'Completá los datos para conectar con el servidor Ekaru'
                    : 'Modificar la configuración del sistema'; ?>
            </div>
        </div>

        <div class="card-body">

            <?php if (!empty($error)): ?>
            <div class="alert alert-error">⚠ <?php echo htmlspecialchars($error); ?></div>
            <?php endif; ?>

            <?php if (!empty($success)): ?>
            <div class="alert alert-success">✓ <?php echo htmlspecialchars($success); ?></div>
            <?php endif; ?>

            <!-- Resultados del test -->
            <?php if (!empty($testResults)): ?>
            <div class="test-results" style="margin-bottom:20px">
                <?php if (isset($testResults['mysql'])): ?>
                <?php $t = $testResults['mysql']; ?>
                <div class="test-item <?php echo $t['ok'] ? 'ok' : 'fail'; ?>">
                    <span class="test-icon"><?php echo $t['ok'] ? '✅' : '❌'; ?></span>
                    <span class="test-label">MySQL</span>
                    <span class="test-msg"><?php echo htmlspecialchars($t['message']); ?></span>
                </div>
                <?php endif; ?>

                <?php if (isset($testResults['backend'])): ?>
                <?php $t = $testResults['backend']; ?>
                <div class="test-item <?php echo $t['ok'] ? 'ok' : 'fail'; ?>">
                    <span class="test-icon"><?php echo $t['ok'] ? '✅' : '❌'; ?></span>
                    <span class="test-label">Backend</span>
                    <span class="test-msg"><?php echo htmlspecialchars($t['message']); ?></span>
                </div>
                <?php endif; ?>
            </div>
            <?php endif; ?>

            <form method="POST" id="configForm">

                <!-- Backend -->
                <div class="section-title">Servidor Ekaru</div>

                <div class="form-group">
                    <label class="form-label">URL Base del servidor</label>
                    <input type="text" name="url_base" class="form-input"
                           placeholder="http://micliente.ekaru.com/ekaru"
                           value="<?php echo htmlspecialchars($current['url_base']); ?>">
                    <div class="form-hint">Sin slash al final. Ejemplo: http://micliente.ekaru.com/ekaru</div>
                </div>

                <div class="form-group">
                    <label class="form-label">API Key</label>
                    <input type="text" name="api_key" class="form-input"
                           placeholder="3_20250602_xxxxxxxxxxxx"
                           value="<?php echo htmlspecialchars($current['api_key']); ?>">
                    <div class="form-hint">Clave de acceso al servidor. La encontrás en el ERP.</div>
                </div>

                <!-- MySQL -->
                <div class="section-title">Base de datos MySQL local</div>

                <div class="form-group">
                    <div class="form-row-3">
                        <div>
                            <label class="form-label">Host</label>
                            <input type="text" name="db_host" class="form-input"
                                   placeholder="localhost"
                                   value="<?php echo htmlspecialchars($current['db_host']); ?>">
                        </div>
                        <div>
                            <label class="form-label">Puerto</label>
                            <input type="text" name="db_port" class="form-input"
                                   placeholder="3306"
                                   value="<?php echo htmlspecialchars($current['db_port']); ?>">
                        </div>
                    </div>
                </div>

                <div class="form-group">
                    <label class="form-label">Nombre de la base de datos</label>
                    <input type="text" name="db_name" class="form-input"
                           placeholder="ekaru_bi"
                           value="<?php echo htmlspecialchars($current['db_name']); ?>">
                    <div class="form-hint">Si no existe, se creará automáticamente.</div>
                </div>

                <div class="form-group">
                    <div class="form-row">
                        <div>
                            <label class="form-label">Usuario</label>
                            <input type="text" name="db_user" class="form-input"
                                   placeholder="root"
                                   value="<?php echo htmlspecialchars($current['db_user']); ?>">
                        </div>
                        <div>
                            <label class="form-label">Contraseña</label>
                            <input type="password" name="db_pass" class="form-input"
                                   placeholder="(vacío si no tiene)"
                                   value="<?php echo htmlspecialchars($current['db_pass']); ?>">
                        </div>
                    </div>
                </div>

                <div class="loading" id="loadingMsg">⏳ Probando conexiones...</div>

                <div class="btn-row">
                    <button type="submit" name="action" value="test"
                            class="btn btn-ghost" onclick="showLoading('Probando conexiones...')">
                        🔗 Probar conexión
                    </button>
                    <button type="submit" name="action" value="save"
                            class="btn btn-primary" onclick="showLoading('Guardando configuración...')">
                        ✓ Guardar y continuar
                    </button>
                </div>

            </form>

        </div>
    </div>

</div>

<script>
function showLoading(msg) {
    var el = document.getElementById('loadingMsg');
    el.textContent = '⏳ ' + msg;
    el.style.display = 'block';
}
</script>
</body>
</html>
