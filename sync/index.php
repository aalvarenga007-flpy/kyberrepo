<?php
/**
 * index.php — Panel de Control Ekaru BI Sync
 * Compatible con PHP 5.5+
 */

if (!defined('BI_ROOT')) define('BI_ROOT', __DIR__);
if (!defined('BI_CODE')) define('BI_CODE', __DIR__);

require_once BI_CODE . '/src/Config.php';
require_once BI_CODE . '/src/Prerequisites.php';

$configPath  = BI_ROOT . '/config.txt';
$configError = null;
$vistas      = array();
$appName     = 'Ekaru BI Sync';
$version     = '1.0.0';
$prereqOk    = false;

try {
    $config   = new Config($configPath);

    // Detectar si no esta configurado aun
    $urlBase = $config->get('url_base');
    $apiKey  = $config->get('api_key');
    if ($urlBase === 'CONFIGURAR' || $apiKey === 'CONFIGURAR' || empty($urlBase) || empty($apiKey)) {
        header('Location: configuracion.php');
        exit;
    }

    $vistas   = $config->getVistas();
    $appName  = $config->getAppName();
    $version  = $config->getVersion();
    $prereq   = new Prerequisites(BI_ROOT);
    $prereqOk = $prereq->allOk();

    // Auto-actualizar vistas.txt si tiene mas de 24hs o no existe
    $vistasPath     = BI_ROOT . '/vistas.txt';
    $vistasAge      = file_exists($vistasPath) ? (time() - filemtime($vistasPath)) : PHP_INT_MAX;
    $vistasAgeHours = $vistasAge / 3600;
    $autoUpdateMsg  = '';

    if ($vistasAgeHours >= 24 && !defined('KYBER_WEB_PANEL')) {
        // Intentar actualizar silenciosamente
        $wsUrl = $config->getVistasWsUrl();
        $ch    = curl_init();
        curl_setopt_array($ch, array(
            CURLOPT_URL            => $wsUrl,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT        => 10,
            CURLOPT_CONNECTTIMEOUT => 5,
            CURLOPT_ENCODING       => 'gzip, deflate',
            CURLOPT_SSL_VERIFYPEER => false,
            CURLOPT_SSL_VERIFYHOST => false,
        ));
        $body    = curl_exec($ch);
        $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);

        if ($body && $httpCode >= 200 && $httpCode < 300) {
            $json = json_decode($body, true);
            if (is_array($json) && !empty($json)) {
                $vistasFiltradas = array();
                foreach ($json as $v) {
                    if (!isset($v['nombre']) || !isset($v['endpoint'])) continue;
                    $vistasFiltradas[] = array(
                        'nombre'   => $v['nombre'],
                        'endpoint' => $v['endpoint'],
                        'idpk'     => isset($v['idpk']) ? $v['idpk'] : '',
                        'keyset'   => isset($v['keyset']) ? $v['keyset'] : false,
                    );
                }
                if (!empty($vistasFiltradas)) {
                    $content = "; Ekaru BI Sync — Vistas\n"
                             . "; Actualizado: " . date('Y-m-d H:i:s') . "\n\n"
                             . "[vistas]\n"
                             . "json = " . json_encode($vistasFiltradas, JSON_UNESCAPED_UNICODE) . "\n";
                    file_put_contents($vistasPath, $content);
                    // Recargar vistas actualizadas
                    $vistas        = $vistasFiltradas;
                    $autoUpdateMsg = 'ok';
                }
            }
        } else {
            $autoUpdateMsg = 'error';
        }
    }

} catch (Exception $e) {
    $configError = $e->getMessage();
}
?>
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title><?php echo htmlspecialchars($appName); ?> v<?php echo $version; ?></title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0f1117;--bg2:#1a1d27;--bg3:#222535;--border:#2e3147;
  --accent:#4f7df3;--accent2:#6c8ff5;
  --green:#22c55e;--yellow:#f59e0b;--red:#ef4444;--orange:#f97316;--blue:#3b82f6;--gray:#64748b;
  --text:#e2e8f0;--text2:#94a3b8;--text3:#64748b;
  --radius:10px;--shadow:0 4px 24px rgba(0,0,0,.4);
}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;min-height:100vh}

/* Header */
.header{background:var(--bg2);border-bottom:1px solid var(--border);padding:14px 28px;display:flex;align-items:center;gap:14px}
.header-logo{width:34px;height:34px;background:var(--accent);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:17px;flex-shrink:0}
.header-title{font-size:17px;font-weight:700}
.header-sub{font-size:11px;color:var(--text3);margin-top:1px}
.header-right{margin-left:auto;display:flex;align-items:center;gap:10px}
.version-badge{font-size:11px;color:var(--text3);background:var(--bg3);border:1px solid var(--border);padding:3px 8px;border-radius:20px}

/* Worker status bar */
.worker-bar{padding:8px 28px;font-size:12px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--border)}
.worker-bar.ok     {background:rgba(34,197,94,.08)}
.worker-bar.warning{background:rgba(245,158,11,.08)}
.worker-bar.error  {background:rgba(239,68,68,.08)}
.worker-bar.never  {background:rgba(100,116,139,.08)}
.worker-bar.running{background:rgba(59,130,246,.08)}

/* Main */
.main{padding:24px 28px;max-width:1500px;margin:0 auto}

/* Stats */
.stats-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin-bottom:24px}
.stat-card{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:18px}
.stat-label{font-size:11px;color:var(--text3);text-transform:uppercase;letter-spacing:.5px}
.stat-value{font-size:26px;font-weight:700;margin-top:5px}
.stat-value.green{color:var(--green)}.stat-value.red{color:var(--red)}
.stat-value.yellow{color:var(--yellow)}.stat-value.blue{color:var(--accent)}.stat-value.gray{color:var(--gray)}

/* Toolbar */
.toolbar{display:flex;align-items:center;gap:10px;margin-bottom:16px;flex-wrap:wrap}
.section-title{font-size:15px;font-weight:600;margin-right:auto}
.refresh-time{font-size:11px;color:var(--text3)}

/* Table */
.table-card{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;box-shadow:var(--shadow)}
table{width:100%;border-collapse:collapse}
thead th{background:var(--bg3);padding:11px 14px;text-align:left;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.5px;color:var(--text3);border-bottom:1px solid var(--border);white-space:nowrap}
thead th:first-child{width:36px;text-align:center}
tbody tr{border-bottom:1px solid var(--border);transition:background .12s}
tbody tr:last-child{border-bottom:none}
tbody tr:hover>td{background:rgba(255,255,255,.02)}
td{padding:13px 14px;vertical-align:middle}

.vista-nombre{font-weight:600}
.vista-ep{font-size:11px;color:var(--text3);font-family:monospace;margin-top:2px}

/* Badges */
.badge{display:inline-flex;align-items:center;gap:5px;padding:3px 9px;border-radius:20px;font-size:12px;font-weight:600;white-space:nowrap}
.badge-ok     {background:rgba(34,197,94,.15);color:var(--green)}
.badge-error  {background:rgba(239,68,68,.15);color:var(--red)}
.badge-running{background:rgba(59,130,246,.15);color:var(--blue)}
.badge-pending{background:rgba(249,115,22,.15);color:var(--orange)}
.badge-paused {background:rgba(245,158,11,.15);color:var(--yellow)}
.badge-never  {background:rgba(100,116,139,.15);color:var(--gray)}
.badge-warning{background:rgba(245,158,11,.15);color:var(--yellow)}

.dot{width:7px;height:7px;border-radius:50%;background:currentColor;flex-shrink:0}
.dot.pulse{animation:pulse 1.4s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}

/* Numbers */
.num{font-variant-numeric:tabular-nums;font-weight:600}
.dim{color:var(--text3);font-size:12px}
.ago{font-size:12px}
.ago.ok{color:var(--green)}.ago.warn{color:var(--yellow)}.ago.late{color:var(--red)}.ago.never{color:var(--gray)}

/* Buttons */
.btn{display:inline-flex;align-items:center;gap:5px;padding:6px 13px;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer;border:none;transition:all .15s;white-space:nowrap}
.btn:disabled{opacity:.4;cursor:not-allowed}
.btn-primary{background:var(--accent);color:#fff}.btn-primary:hover:not(:disabled){background:var(--accent2)}
.btn-ghost{background:transparent;border:1px solid var(--border);color:var(--text2)}.btn-ghost:hover:not(:disabled){background:var(--bg3);color:var(--text)}
.btn-danger{background:rgba(239,68,68,.15);border:1px solid rgba(239,68,68,.3);color:var(--red)}.btn-danger:hover:not(:disabled){background:rgba(239,68,68,.25)}
.btn-warn{background:rgba(245,158,11,.15);border:1px solid rgba(245,158,11,.3);color:var(--yellow)}.btn-warn:hover:not(:disabled){background:rgba(245,158,11,.25)}
.btn-lg{padding:8px 18px;font-size:13px}

/* Progress */
.progress-wrap{min-width:180px}
.progress-bar-bg{background:var(--bg3);border-radius:4px;height:5px;overflow:hidden;margin-bottom:4px}
.progress-bar-fill{height:100%;background:var(--accent);border-radius:4px;width:0;transition:width .3s}
.progress-bar-fill.ind{width:35%!important;animation:ind 1.1s ease-in-out infinite}
@keyframes ind{0%{margin-left:-35%}100%{margin-left:100%}}
.progress-text{font-size:11px;color:var(--text2);display:flex;justify-content:space-between;gap:8px}

/* Inline log */
.log-row{display:none}
.log-row.open{display:table-row}
.log-row td{padding:10px 14px 10px 40px;background:var(--bg)}
.log-lines{max-height:160px;overflow-y:auto;font-family:'Consolas','Courier New',monospace;font-size:11px;line-height:1.7}
.ll{}.ll.err{color:var(--red)}.ll.ok{color:var(--green)}.ll.wrn{color:var(--yellow)}.ll.dim{color:var(--text3)}

/* Prereq banner */
.prereq-banner{background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.3);border-radius:var(--radius);padding:14px 18px;margin-bottom:20px;display:flex;align-items:center;gap:10px}

/* Alert */
.alert{padding:13px 17px;border-radius:var(--radius);margin-bottom:20px;border:1px solid}
.alert-error{background:rgba(239,68,68,.1);border-color:rgba(239,68,68,.3);color:var(--red)}

/* Modal */
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:100;align-items:center;justify-content:center}
.modal-overlay.open{display:flex}
.modal{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);width:90vw;max-width:860px;max-height:82vh;display:flex;flex-direction:column;box-shadow:var(--shadow)}
.modal-header{padding:15px 20px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between}
.modal-title{font-weight:700;font-size:15px}
.modal-close{background:none;border:none;color:var(--text2);font-size:22px;cursor:pointer;line-height:1}.modal-close:hover{color:var(--text)}
.modal-body{flex:1;overflow-y:auto;padding:16px 20px}
.prereq-list{display:flex;flex-direction:column;gap:8px}
.prereq-item{display:flex;align-items:flex-start;gap:10px;padding:10px 14px;border-radius:8px;background:var(--bg3);border:1px solid var(--border)}
.prereq-icon{font-size:16px;flex-shrink:0;margin-top:1px}
.prereq-label{font-weight:600;font-size:13px}
.prereq-msg{font-size:12px;color:var(--text2);margin-top:2px}
.worker-cmd{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:10px 14px;font-family:monospace;font-size:12px;color:var(--accent);margin-top:8px;word-break:break-all}

/* Tabs */
.tabs{display:flex;gap:2px;border-bottom:1px solid var(--border);margin-bottom:16px}
.tab{padding:8px 16px;font-size:13px;cursor:pointer;border:none;background:none;color:var(--text2);border-bottom:2px solid transparent;transition:all .15s}
.tab.active{color:var(--text);border-bottom-color:var(--accent)}
.tab:hover:not(.active){color:var(--text)}
.tab-content{display:none}.tab-content.active{display:block}

/* Checkbox */
input[type=checkbox]{width:16px;height:16px;cursor:pointer;accent-color:var(--accent)}
</style>
</head>
<body>

<!-- Header -->
<header class="header">
  <div class="header-logo">📊</div>
  <div>
    <div class="header-title"><?php echo htmlspecialchars($appName); ?></div>
    <div class="header-sub">Panel de Sincronizacion BI</div>
  </div>
  <div class="header-right">
    <?php if (defined('KYBER_WEB_PANEL')): ?><a href="<?= htmlspecialchars(panel_app_path(), ENT_QUOTES, 'UTF-8') ?>" class="btn btn-ghost">← Volver a Kyber</a><?php endif; ?>
    <span class="version-badge">v<?php echo defined('KYBER_APP_VERSION') ? KYBER_APP_VERSION : $version; ?></span>
    <button class="btn btn-ghost" onclick="showPrerequisites()" title="Verificar sistema">🔧 Sistema</button>
    <a href="configuracion.php" class="btn btn-ghost" title="Configuracion">⚙ Configuracion</a>
    <button class="btn btn-ghost" onclick="refreshStatus()">↻ Actualizar</button>
  </div>
</header>

<?php if (defined('KYBER_WEB_PANEL')): ?>
<div style="background:#27324b;padding:14px 28px;color:#f4f6ff;font-size:14px">
  <strong><?= getenv('KYBER_PANEL_ENV') === 'production' ? 'Panel de producción' : 'Panel de pruebas' ?> · Datos reales de <?= htmlspecialchars($appName, ENT_QUOTES, 'UTF-8') ?></strong><br>
  Mirar el estado no inicia una sincronización. Para traer lo más reciente, usá <strong>Sync</strong> en una fila
  o <strong>Sincronizar todo</strong>. Esas acciones actualizan la misma base que consulta Kyber oficial.
</div>
<?php endif; ?>

<?php if (isset($_GET['configurado'])): ?>
<div style="background:rgba(34,197,94,.1);border-bottom:1px solid rgba(34,197,94,.2);padding:10px 28px;font-size:13px;color:#22c55e;display:flex;align-items:center;gap:8px">
  ✓ Configuracion guardada correctamente.
  <?php if (isset($_GET['vistas_error'])): ?>
  &nbsp;⚠ No se pudieron descargar las vistas: <?php echo htmlspecialchars($_GET['vistas_error']); ?> — usar el boton "↻ Actualizar vistas".
  <?php endif; ?>
</div>
<?php endif; ?>

<!-- Worker bar -->
<div class="worker-bar never" id="workerBar">
  <span id="workerDot">⚫</span>
  <span id="workerMsg">Verificando worker...</span>
  <span style="margin-left:auto;font-size:11px" id="workerTime"></span>
</div>

<main class="main">

<?php if ($configError): ?>
<div class="alert alert-error">
  <strong>⚠ Error de configuracion:</strong> <?php echo htmlspecialchars($configError); ?><br>
  <small>Verifique que <code>config.txt</code> exista en <code><?php echo BI_ROOT; ?></code></small>
</div>
<?php endif; ?>

<?php if (!$prereqOk && !$configError): ?>
<div class="prereq-banner">
  <span>⚠</span>
  <span>Hay problemas en la configuracion del sistema. <a href="#" onclick="showPrerequisites();return false;" style="color:var(--accent)">Ver detalles</a></span>
</div>
<?php endif; ?>

<!-- Stats -->
<div class="stats-row">
  <div class="stat-card"><div class="stat-label">Vistas</div><div class="stat-value blue" id="statTotal"><?php echo count($vistas); ?></div></div>
  <div class="stat-card"><div class="stat-label">Al dia</div><div class="stat-value green" id="statOk">—</div></div>
  <div class="stat-card"><div class="stat-label">Atrasadas</div><div class="stat-value yellow" id="statLate">—</div></div>
  <div class="stat-card"><div class="stat-label">Con error</div><div class="stat-value red" id="statErr">—</div></div>
  <div class="stat-card"><div class="stat-label">En cola</div><div class="stat-value orange" id="statQueue">—</div></div>
  <div class="stat-card"><div class="stat-label">Registros totales</div><div class="stat-value blue" id="statRec">—</div></div>
</div>

<div id="syncSummary" role="status" aria-live="polite" style="padding:16px;margin-bottom:18px;background:var(--surface);border:1px solid var(--border);border-radius:10px">Consultando el estado actual…</div>

<!-- Toolbar -->
<div class="toolbar">
  <span class="section-title">📋 Vistas disponibles</span>
  <span class="refresh-time" id="lastRefresh"></span>
  <button class="btn btn-ghost" id="btnUpdateVistas" onclick="updateVistas()" title="Descargar lista de vistas actualizada desde el servidor">↻ Actualizar vistas</button>
  <span id="vistasUpdateMsg" style="font-size:12px;display:none"></span>
  <button class="btn btn-ghost" onclick="checkAll(true)">☑ Marcar todo</button>
  <button class="btn btn-ghost" onclick="checkAll(false)">☐ Desmarcar</button>
  <button class="btn btn-primary btn-lg" onclick="syncMarked()" id="btnSyncMarked">▶ Sincronizar marcadas</button>
  <button class="btn btn-primary btn-lg" onclick="syncAll()"    id="btnSyncAll">⚡ Sincronizar todo</button>
</div>

<!-- Table -->
<div class="table-card">
<table id="vistasTable">
<thead>
  <tr>
    <th><input type="checkbox" id="chkAll" onchange="checkAll(this.checked)" title="Seleccionar todo"></th>
    <th>Vista</th>
    <th>Estado</th>
    <th>Registros</th>
    <th>Ultima Sync</th>
    <th>Ultimo Batch</th>
    <th>Progreso</th>
    <th>Acciones</th>
  </tr>
</thead>
<tbody id="vistasBody">
<?php foreach ($vistas as $v): ?>
<?php $k = rawurlencode($v['nombre']); ?>
<tr id="row-<?php echo $k; ?>" data-vista="<?php echo htmlspecialchars($v['nombre'], ENT_QUOTES); ?>">
  <td style="text-align:center">
    <input type="checkbox" class="vista-chk" data-vista="<?php echo htmlspecialchars($v['nombre'], ENT_QUOTES); ?>"
           id="chk-<?php echo $k; ?>" onchange="saveChecked()">
  </td>
  <td>
    <div class="vista-nombre"><?php echo htmlspecialchars($v['nombre']); ?></div>
    <div class="vista-ep"><?php echo htmlspecialchars(isset($v['endpoint']) ? $v['endpoint'] : ''); ?></div>
  </td>
  <td id="status-<?php echo $k; ?>"><span class="badge badge-never"><span class="dot"></span>Nunca</span></td>
  <td id="records-<?php echo $k; ?>" class="num">—</td>
  <td id="lastsync-<?php echo $k; ?>" class="ago never">—</td>
  <td id="lastbatch-<?php echo $k; ?>" class="dim">—</td>
  <td id="prog-<?php echo $k; ?>">
    <div class="progress-wrap" style="display:none" id="pwrap-<?php echo $k; ?>">
      <div class="progress-bar-bg"><div class="progress-bar-fill ind" id="pbar-<?php echo $k; ?>"></div></div>
      <div class="progress-text">
        <span id="pstatus-<?php echo $k; ?>"></span>
        <span id="pcount-<?php echo $k; ?>"></span>
      </div>
    </div>
  </td>
  <td>
    <div style="display:flex;gap:5px;flex-wrap:wrap" id="actions-<?php echo $k; ?>">
      <button class="btn btn-primary" id="btnSync-<?php echo $k; ?>"
              data-nombre="<?php echo htmlspecialchars($v['nombre'], ENT_QUOTES); ?>"
              onclick="enqueueVista(this.getAttribute('data-nombre'))">▶ Sync</button>
      <button class="btn btn-ghost" title="Test conexion"
              data-nombre="<?php echo htmlspecialchars($v['nombre'], ENT_QUOTES); ?>"
              onclick="testConnection(this.getAttribute('data-nombre'))">🔗</button>
      <button class="btn btn-ghost" title="Ver log"
              data-nombre="<?php echo htmlspecialchars($v['nombre'], ENT_QUOTES); ?>"
              onclick="toggleLog(this.getAttribute('data-nombre'))">📄</button>
      <button class="btn btn-ghost" title="Historial"
              data-nombre="<?php echo htmlspecialchars($v['nombre'], ENT_QUOTES); ?>"
              onclick="showHistory(this.getAttribute('data-nombre'))">📅</button>
    </div>
  </td>
</tr>
<!-- Log row -->
<tr class="log-row" id="logrow-<?php echo $k; ?>">
  <td colspan="8">
    <div id="currentState-<?php echo $k; ?>" style="padding:10px 16px;color:var(--green)" role="status"></div>
    <div class="dim" style="padding:0 16px;font-size:11px">Historial de mensajes de esta pantalla (no es el estado actual)</div>
    <div class="log-lines" id="loglines-<?php echo $k; ?>">
      <div class="ll dim">Sin actividad reciente.</div>
    </div>
  </td>
</tr>
<?php endforeach; ?>
</tbody>
</table>
</div>

</main>

<!-- Modal -->
<div class="modal-overlay" id="modalOverlay" onclick="maybeCloseModal(event)">
  <div class="modal">
    <div class="modal-header">
      <span class="modal-title" id="modalTitle">Sistema</span>
      <button class="modal-close" onclick="closeModal()">×</button>
    </div>
    <div class="modal-body" id="modalBody">Cargando...</div>
  </div>
</div>

<script>
<?php readfile(BI_CODE . '/ui_status.js'); ?>
// ── Estado global ─────────────────────────────────────────────────────────────
var POLL_INTERVAL = 3000;
var pollTimer     = null;
var logOpen       = {};
var syncQueue     = [];
var syncIndex     = 0;
var syncing       = false;

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function() {
    loadChecked();
    refreshStatus();
    pollTimer = setInterval(refreshStatus, POLL_INTERVAL);
});

// ── Helpers ───────────────────────────────────────────────────────────────────
// Debe coincidir con rawurlencode() de PHP
// PHP codifica ( ) con %28 %29 pero JS encodeURIComponent no los codifica
function enc(name) {
    return encodeURIComponent(name)
        .replace(/\(/g, '%28')
        .replace(/\)/g, '%29')
        .replace(/!/g,  '%21')
        .replace(/'/g,  '%27')
        .replace(/~/g,  '%7E');
}

function fmt(n) {
    if (n == null || n === '' || n === '—') return '—';
    return Number(n).toLocaleString('es-PY');
}

function timeAgo(dateStr) {
    if (!dateStr) return { text: '—', cls: 'never' };
    var d    = new Date(dateStr.replace(' ','T'));
    var diff = Math.floor((Date.now() - d.getTime()) / 1000);
    var h    = diff / 3600;
    var text;
    if (diff < 60)    text = 'hace ' + diff + 's';
    else if (diff < 3600) text = 'hace ' + Math.floor(diff/60) + 'min';
    else if (diff < 86400) text = 'hace ' + Math.floor(diff/3600) + 'h';
    else text = d.toLocaleDateString('es-PY');
    var cls = h < 24 ? 'ok' : (h < 48 ? 'warn' : 'late');
    return { text: text, cls: cls };
}

function api(params, cb) {
    var url = 'sync.php?' + Object.keys(params).map(function(k){ return k+'='+enc(params[k]); }).join('&');
    var xhr = new XMLHttpRequest();
    var protectedPanel = <?php echo defined('KYBER_WEB_PANEL') ? 'true' : 'false'; ?>;
    xhr.open(protectedPanel ? 'POST' : 'GET', protectedPanel ? 'sync.php' : url);
    if (protectedPanel) {
        xhr.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded');
        xhr.setRequestHeader('X-Kyber-CSRF', <?php echo json_encode(defined('KYBER_PANEL_CSRF') ? KYBER_PANEL_CSRF : ''); ?>);
    }
    xhr.onload = function() {
        try { cb(null, JSON.parse(xhr.responseText)); }
        catch(e) { cb(e, null); }
    };
    xhr.onerror = function() { cb(new Error('Network error'), null); };
    xhr.send(protectedPanel ? Object.keys(params).map(function(k){ return enc(k)+'='+enc(params[k]); }).join('&') : null);
}

// ── Refresh estado ────────────────────────────────────────────────────────────
// ── Actualizar vistas desde backend ──────────────────────────────────────────
function updateVistas() {
    var btn = document.getElementById('btnUpdateVistas');
    var msg = document.getElementById('vistasUpdateMsg');

    btn.disabled      = true;
    btn.textContent   = '↻ Actualizando...';
    msg.style.display = 'none';

    api({ action: 'update_vistas' }, function(err, res) {
        btn.disabled    = false;
        btn.textContent = '↻ Actualizar vistas';

        if (!err && res && res.ok) {
            msg.style.display = 'inline';
            msg.style.color   = 'var(--green)';
            msg.textContent   = '✓ ' + res.message + ' — recargando...';
            setTimeout(function() { location.reload(); }, 1500);
        } else {
            msg.style.display = 'inline';
            msg.style.color   = 'var(--red)';
            msg.textContent   = '✗ ' + (res ? res.error : 'Error de conexion');
            setTimeout(function() { msg.style.display = 'none'; }, 5000);
        }
    });
}

// ── Refresh estado ────────────────────────────────────────────────────────────
function refreshStatus() {
    api({ action: 'status' }, function(err, res) {
        if (err || !res || !res.ok) {
            document.getElementById('syncSummary').textContent = 'No se pudo actualizar el estado. No podemos confirmar si terminó: recargá el panel o volvé a abrirlo desde Kyber.';
            return;
        }
        applyStatus(res.vistas);
        updateWorkerBar(res.worker);
        document.getElementById('lastRefresh').textContent = '— ' + new Date().toLocaleTimeString('es-PY');
    });
}

function applyStatus(vistas) {
    var ok=0, late=0, err=0, queue=0, rec=0;

    vistas.forEach(function(v) {
        var k      = enc(v.nombre);
        var status = v.last_sync_status || 'never';
        var qJob   = v.queue_job;
        var current = document.getElementById('currentState-' + k);
        if (current) current.textContent = kyberCurrentState(v);

        // Determinar estado visual
        var dispStatus = status;
        if (qJob) {
            if (qJob.status === 'running')  dispStatus = 'running';
            else if (qJob.status === 'pending') dispStatus = 'pending';
            else if (qJob.status === 'paused')  dispStatus = 'paused';
        }

        // Badge
        var badgeMap = {
            ok:      ['badge-ok',      '<span class="dot"></span>Al dia'],
            error:   ['badge-error',   '<span class="dot"></span>Error'],
            running: ['badge-running', '<span class="dot pulse"></span>Sincronizando'],
            pending: ['badge-pending', '<span class="dot pulse"></span>En cola'],
            paused:  ['badge-paused',  '<span class="dot"></span>Pausada'],
            never:   ['badge-never',   '<span class="dot"></span>Nunca'],
        };
        var bInfo = badgeMap[dispStatus] || badgeMap.never;
        var el = document.getElementById('status-' + k);
        if (el) {
            var title = '';
            if (dispStatus === 'error' && v.last_error_message) {
                title = ' title="' + v.last_error_message.replace(/"/g, '&quot;').substring(0, 200) + '"';
            }
            el.innerHTML = '<span class="badge ' + bInfo[0] + '"' + title + '>' + bInfo[1] + '</span>';
            // Si hay error mostrar icono de info
            if (dispStatus === 'error' && v.last_error_message) {
                el.innerHTML += ' <span style="color:var(--red);cursor:pointer;font-size:13px" '
                    + 'title="' + v.last_error_message.replace(/"/g, '&quot;').substring(0, 300) + '"'
                    + '>ⓘ</span>';
            }
        }

        // Records
        var rEl = document.getElementById('records-' + k);
        if (rEl) rEl.textContent = fmt(v.total_records_local);

        // Last sync
        var lEl = document.getElementById('lastsync-' + k);
        if (lEl) {
            var ago = timeAgo(v.last_sync_end);
            lEl.textContent = ago.text;
            lEl.className   = 'ago ' + ago.cls;
        }

        // Last batch
        var bEl = document.getElementById('lastbatch-' + k);
        if (bEl) {
            var ins = v.last_records_inserted || 0;
            var upd = v.last_records_updated  || 0;
            bEl.textContent = (ins || upd) ? '+' + fmt(ins) + ' / ~' + fmt(upd) : '—';
        }

        // Botones segun estado del job
        updateActionButtons(k, v.nombre, dispStatus, qJob);

        // Progress bar
        updateProgressFromJob(k, qJob);

        // Contadores
        if (dispStatus === 'running' || dispStatus === 'pending') queue++;
        if (status === 'ok') {
            var ago2 = timeAgo(v.last_sync_end);
            if (ago2.cls === 'ok') ok++; else late++;
        }
        if (status === 'error') err++;
        rec += parseInt(v.total_records_local) || 0;
    });

    document.getElementById('statOk').textContent    = ok;
    document.getElementById('statLate').textContent  = late;
    document.getElementById('statErr').textContent   = err;
    document.getElementById('statQueue').textContent = queue;
    document.getElementById('statRec').textContent   = fmt(rec);
    document.getElementById('syncSummary').textContent = kyberSyncSummary(vistas);
}

function updateActionButtons(k, nombre, status, qJob) {
    var syncBtn = document.getElementById('btnSync-' + k);
    if (!syncBtn) return;

    if (status === 'running') {
        syncBtn.innerHTML = '⏸ Pausar';
        syncBtn.onclick   = function() { pauseVista(nombre); };
        syncBtn.disabled  = false;

        // Mostrar boton forzar cancelar si lleva mas de 10 min sin heartbeat
        var actEl = document.getElementById('actions-' + k);
        if (actEl && qJob && qJob.worker_heartbeat && !document.getElementById('btnForce-' + k)) {
            var lastBeat = new Date(qJob.worker_heartbeat.replace(' ', 'T'));
            var diffMin  = (Date.now() - lastBeat.getTime()) / 60000;

            if (diffMin > 10) {
                var btnForce = document.createElement('button');
                btnForce.className = 'btn btn-danger';
                btnForce.id        = 'btnForce-' + k;
                btnForce.setAttribute('data-nombre', nombre);
                btnForce.innerHTML = '✕ Cancelar';
                btnForce.title     = 'Worker colgado. Forzar cancelacion.';
                btnForce.onclick   = function() {
                    forceCancel(this.getAttribute('data-nombre'));
                };
                actEl.appendChild(btnForce);
            }
        }

    } else if (status === 'paused') {
        var actEl = document.getElementById('actions-' + k);
        if (actEl && !document.getElementById('btnResume-' + k)) {
            // Agregar botones retomar/reiniciar
            var r1 = document.createElement('button');
            r1.className = 'btn btn-warn'; r1.id = 'btnResume-' + k;
            r1.innerHTML = '▶ Retomar'; r1.onclick = function() { resumeVista(nombre); };

            var r2 = document.createElement('button');
            r2.className = 'btn btn-danger'; r2.id = 'btnRestart-' + k;
            r2.innerHTML = '↺ Reiniciar'; r2.onclick = function() { restartVista(nombre); };

            actEl.insertBefore(r2, actEl.firstChild);
            actEl.insertBefore(r1, actEl.firstChild);
        }
        syncBtn.innerHTML = '▶ Sync'; syncBtn.onclick = function() { enqueueVista(nombre); };
        syncBtn.disabled  = true;

    } else if (status === 'pending') {
        syncBtn.innerHTML = '⏳ En cola'; syncBtn.disabled = true;

    } else {
        // Limpiar botones de retomar si existen
        var r1 = document.getElementById('btnResume-'  + k);
        var r2 = document.getElementById('btnRestart-' + k);
        if (r1) r1.parentNode.removeChild(r1);
        if (r2) r2.parentNode.removeChild(r2);

        syncBtn.innerHTML = '▶ Sync';
        syncBtn.disabled  = false;
        syncBtn.onclick   = function() { enqueueVista(nombre); };
    }
}

function updateProgressFromJob(k, qJob) {
    var wrap = document.getElementById('pwrap-' + k);
    if (!wrap) return;

    if (!qJob || (qJob.status !== 'running' && qJob.status !== 'pending')) {
        wrap.style.display = 'none';
        return;
    }

    wrap.style.display = 'block';
    var pbar    = document.getElementById('pbar-' + k);
    var pstatus = document.getElementById('pstatus-' + k);
    var pcount  = document.getElementById('pcount-' + k);

    if (qJob.status === 'pending') {
        if (pbar) { pbar.classList.add('ind'); pbar.style.width = ''; }
        if (pstatus) pstatus.textContent = '⏳ En cola...';
        if (pcount)  pcount.textContent  = '';
    } else {
        if (pbar) { pbar.classList.remove('ind'); pbar.style.width = '60%'; }
        if (pstatus) pstatus.textContent = '↓ Pagina ' + (qJob.current_page || 1);
        if (pcount)  pcount.textContent  = fmt(qJob.records_downloaded) + ' desc.';
    }
}

// ── Worker bar ────────────────────────────────────────────────────────────────
function updateWorkerBar(worker) {
    if (!worker) return;
    var bar  = document.getElementById('workerBar');
    var dot  = document.getElementById('workerDot');
    var msg  = document.getElementById('workerMsg');
    var time = document.getElementById('workerTime');

    var icons   = { ok:'🟢', warning:'🟡', error:'🔴', never:'⚫', running:'🔵' };
    var classes = { ok:'ok', warning:'warning', error:'error', never:'never', running:'running' };

    bar.className = 'worker-bar ' + (classes[worker.status] || 'never');
    dot.textContent = icons[worker.status] || '⚫';
    msg.textContent = worker.message;
    time.textContent = worker.last_run ? 'Ultima vez: ' + worker.last_run : '';
}

// ── Encolar vista ─────────────────────────────────────────────────────────────
function enqueueVista(nombre) {
    if (!syncing && <?php echo defined('KYBER_WEB_PANEL') ? 'true' : 'false'; ?>
        && !confirm('¿Traer ahora los datos más recientes de ' + nombre + '?\nEsto actualizará la base real que consulta Kyber.')) return;
    addLogLine(nombre, 'Encolando sincronizacion...', 'dim');
    openLog(nombre);

    api({ action: 'enqueue', vista: nombre }, function(err, res) {
        if (err || !res || !res.ok) {
            addLogLine(nombre, '✗ Error: ' + (res ? res.error : 'Error de red'), 'err');
            return;
        }
        var r = res.result;
        if (r.queued) {
            addLogLine(nombre, '✓ Encolado — job #' + r.job_id, 'ok');
        } else {
            addLogLine(nombre, 'ℹ ' + r.message, 'dim');
        }
        refreshStatus();
    });
}

// ── Pausa ─────────────────────────────────────────────────────────────────────
function pauseVista(nombre) {
    api({ action: 'pause', vista: nombre }, function(err, res) {
        addLogLine(nombre, '⏸ Pausa solicitada...', 'wrn');
        refreshStatus();
    });
}

function resumeVista(nombre) {
    api({ action: 'resume', vista: nombre }, function(err, res) {
        if (!err && res && res.ok) {
            addLogLine(nombre, '▶ Retomando sincronizacion...', 'ok');
        }
        refreshStatus();
    });
}

function restartVista(nombre) {
    if (!confirm('¿Reiniciar sincronizacion de "' + nombre + '"?\nSe descartara el progreso actual y empezara desde cero.')) return;

    api({ action: 'restart', vista: nombre }, function(err, res) {
        if (!err && res && res.ok) {
            addLogLine(nombre, '↺ Reiniciando desde cero...', 'wrn');
        }
        refreshStatus();
    });
}

function forceCancel(nombre) {
    if (!confirm('¿Forzar cancelacion de "' + nombre + '"?\n\nEl worker parece estar colgado.\n- Si tiene progreso guardado quedara en estado Pausada y podra retomar.\n- Si no tiene progreso quedara en Error y debera reiniciar.')) return;

    api({ action: 'force_cancel', vista: nombre }, function(err, res) {
        if (!err && res && res.ok) {
            addLogLine(nombre, '✕ Cancelacion forzada: ' + res.message, 'wrn');
        } else {
            addLogLine(nombre, '✗ Error al cancelar: ' + (res ? res.error : 'Error de red'), 'err');
        }
        refreshStatus();
    });
}

// ── Test conexion ─────────────────────────────────────────────────────────────
function testConnection(nombre) {
    addLogLine(nombre, '🔗 Probando conexion...', 'dim');
    openLog(nombre);

    api({ action: 'test_connection', vista: nombre }, function(err, res) {
        if (err || !res) {
            addLogLine(nombre, '✗ Error de red', 'err');
            return;
        }
        if (res.ok) {
            addLogLine(nombre, '✓ Conexion OK — ' + res.records + ' registros de muestra recibidos', 'ok');
        } else {
            addLogLine(nombre, '✗ Error: ' + res.error, 'err');
        }
    });
}

// ── Sincronizar todo / marcadas ───────────────────────────────────────────────
function syncAll() {
    var rows = document.querySelectorAll('#vistasTable [data-vista]');
    var names = [];
    for (var i=0; i<rows.length; i++) names.push(rows[i].getAttribute('data-vista'));
    startQueue(names);
}

function syncMarked() {
    var chks  = document.querySelectorAll('.vista-chk:checked');
    var names = [];
    for (var i=0; i<chks.length; i++) names.push(chks[i].getAttribute('data-vista'));
    if (!names.length) { alert('Marque al menos una vista para sincronizar.'); return; }
    startQueue(names);
}

function startQueue(names) {
    if (<?php echo defined('KYBER_WEB_PANEL') ? 'true' : 'false'; ?>
        && !confirm('¿Actualizar ahora los ' + names.length + ' conjuntos seleccionados?\nEsto actualizará la base real que consulta Kyber.')) return;
    syncQueue = names.slice();
    syncIndex = 0;
    syncing   = true;
    disableMassButtons(true);
    enqueueNext();
}

function enqueueNext() {
    if (syncIndex >= syncQueue.length) {
        syncing = false;
        disableMassButtons(false);
        refreshStatus();
        return;
    }
    var nombre = syncQueue[syncIndex++];
    enqueueVista(nombre);
    setTimeout(enqueueNext, 400);
}

function disableMassButtons(disabled) {
    document.getElementById('btnSyncAll').disabled    = disabled;
    document.getElementById('btnSyncMarked').disabled = disabled;
}

// ── Checkboxes ────────────────────────────────────────────────────────────────
function checkAll(val) {
    var chks = document.querySelectorAll('.vista-chk');
    for (var i=0; i<chks.length; i++) chks[i].checked = val;
    var ca = document.getElementById('chkAll');
    if (ca) ca.checked = val;
    saveChecked();
}

function saveChecked() {
    var chks    = document.querySelectorAll('.vista-chk');
    var checked = [];
    for (var i=0; i<chks.length; i++) {
        if (chks[i].checked) checked.push(chks[i].getAttribute('data-vista'));
    }
    try { localStorage.setItem('bi_checked', JSON.stringify(checked)); } catch(e){}
}

function loadChecked() {
    try {
        var saved = JSON.parse(localStorage.getItem('bi_checked') || '[]');
        saved.forEach(function(nombre) {
            var el = document.getElementById('chk-' + enc(nombre));
            if (el) el.checked = true;
        });
    } catch(e) {}
}

// ── Log inline ────────────────────────────────────────────────────────────────
function toggleLog(nombre) {
    if (logOpen[nombre]) closeLog(nombre);
    else openLog(nombre);
}

function openLog(nombre) {
    var row = document.getElementById('logrow-' + enc(nombre));
    if (row) { row.classList.add('open'); logOpen[nombre] = true; }
}

function closeLog(nombre) {
    var row = document.getElementById('logrow-' + enc(nombre));
    if (row) { row.classList.remove('open'); logOpen[nombre] = false; }
}

function clearLogLines(nombre) {
    var el = document.getElementById('loglines-' + enc(nombre));
    if (el) el.innerHTML = '';
}

function addLogLine(nombre, text, cls) {
    var el = document.getElementById('loglines-' + enc(nombre));
    if (!el) return;
    var div = document.createElement('div');
    div.className   = 'll ' + (cls || '');
    div.textContent = text;
    el.appendChild(div);
    el.scrollTop = el.scrollHeight;
}

// ── Historial ─────────────────────────────────────────────────────────────────
function showHistory(nombre) {
    openModal('Historial: ' + nombre, '<div style="color:var(--text3)">Cargando...</div>');

    api({ action: 'history', vista: nombre }, function(err, res) {
        if (err || !res || !res.ok) {
            document.getElementById('modalBody').innerHTML = '<div style="color:var(--red)">Error al cargar historial.</div>';
            return;
        }
        var h   = res.history;
        var html = '';

        if (!h.length) {
            html = '<div style="color:var(--text3)">Sin historial de sincronizaciones.</div>';
        } else {
            html = '<table style="width:100%;border-collapse:collapse;font-size:13px">'
                 + '<tr style="color:var(--text3);border-bottom:1px solid var(--border)">'
                 + '<th style="padding:8px 10px;text-align:left">Inicio</th>'
                 + '<th style="padding:8px 10px;text-align:left">Estado</th>'
                 + '<th style="padding:8px 10px;text-align:right">Paginas</th>'
                 + '<th style="padding:8px 10px;text-align:right">Insertados</th>'
                 + '<th style="padding:8px 10px;text-align:right">Actualizados</th>'
                 + '<th style="padding:8px 10px;text-align:right">Tiempo</th>'
                 + '</tr>';

            h.forEach(function(row) {
                var clr = row.status === 'ok' ? 'var(--green)' : 'var(--red)';
                html += '<tr style="border-bottom:1px solid var(--border)">'
                      + '<td style="padding:8px 10px">' + (row.started_at || '—') + '</td>'
                      + '<td style="padding:8px 10px;color:' + clr + '">' + row.status + '</td>'
                      + '<td style="padding:8px 10px;text-align:right">' + fmt(row.pages_downloaded) + '</td>'
                      + '<td style="padding:8px 10px;text-align:right">' + fmt(row.records_inserted) + '</td>'
                      + '<td style="padding:8px 10px;text-align:right">' + fmt(row.records_updated)  + '</td>'
                      + '<td style="padding:8px 10px;text-align:right">' + row.elapsed_seconds + 's</td>'
                      + '</tr>';
            });
            html += '</table>';
        }
        document.getElementById('modalBody').innerHTML = html;
    });
}

// ── Prerequisitos ─────────────────────────────────────────────────────────────
function showPrerequisites() {
    openModal('Verificacion del sistema', '<div style="color:var(--text3)">Verificando...</div>');

    api({ action: 'prerequisites' }, function(err, res) {
        if (err || !res) {
            document.getElementById('modalBody').innerHTML = '<div style="color:var(--red)">Error al verificar.</div>';
            return;
        }

        var html = '<div class="prereq-list">';
        res.checks.forEach(function(c) {
            var icon = c.ok ? '✅' : (c.critical ? '❌' : '⚠️');
            html += '<div class="prereq-item">'
                  + '<span class="prereq-icon">' + icon + '</span>'
                  + '<div><div class="prereq-label">' + c.label + '</div>'
                  + '<div class="prereq-msg">' + c.message + '</div>'
                  + '</div></div>';
        });
        html += '</div>';

        // Comando worker
        if (res.env && res.env.worker_cmd) {
            html += '<div style="margin-top:20px">'
                  + '<div style="font-weight:600;margin-bottom:8px">Comando para Task Scheduler:</div>'
                  + '<div class="worker-cmd">' + escHtml(res.env.worker_cmd) + '</div>'
                  + '<div style="color:var(--text3);font-size:12px;margin-top:8px">'
                  + 'Configurar en Panel de Control → Herramientas administrativas → Programador de tareas → cada 1 minuto'
                  + '</div></div>';
        }

        document.getElementById('modalBody').innerHTML = html;
    });
}

function escHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Modal ─────────────────────────────────────────────────────────────────────
function openModal(title, body) {
    document.getElementById('modalTitle').textContent   = title;
    document.getElementById('modalBody').innerHTML      = body;
    document.getElementById('modalOverlay').classList.add('open');
}

function closeModal() {
    document.getElementById('modalOverlay').classList.remove('open');
}

function maybeCloseModal(e) {
    if (e.target === document.getElementById('modalOverlay')) closeModal();
}
</script>
</body>
</html>
