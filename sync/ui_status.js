// Pure functions: the visible current state is separate from historical messages.
function kyberCurrentState(v) {
    var job = v.queue_job;
    if (job && job.status === 'running') return 'Sincronizando ahora…';
    if (job && job.status === 'pending') return 'En espera: todavía no terminó.';
    if (job && job.status === 'paused') return 'Sincronización pausada: todavía no terminó.';
    if (v.last_sync_status === 'error') return 'La última sincronización terminó con error. Revisá el historial.';
    if (v.last_sync_status === 'ok' && v.last_sync_end) return '✓ Última sincronización completada: ' + v.last_sync_end;
    if (v.last_sync_status === 'running') return 'Sincronizando ahora…';
    return 'Todavía no hay una sincronización completada.';
}

function kyberSyncSummary(views) {
    var active = 0, paused = 0, errors = 0, done = 0, latest = '';
    views.forEach(function(v) {
        var job = v.queue_job;
        if (job && job.status === 'paused') paused++;
        else if ((job && ['pending', 'running'].indexOf(job.status) !== -1) || v.last_sync_status === 'running') active++;
        else if (v.last_sync_status === 'error') errors++;
        else if (v.last_sync_status === 'ok' && v.last_sync_end) {
            done++;
            if (v.last_sync_end > latest) latest = v.last_sync_end;
        }
    });
    if (active) return 'Sincronización en curso: ' + active + ' conjuntos de datos en proceso o en cola.' + (paused ? ' Hay ' + paused + ' pausados.' : '') + (errors ? ' Hay ' + errors + ' con error.' : '');
    if (paused || errors) return 'Requiere atención: ' + paused + ' pausados y ' + errors + ' con error. No está todo completado.';
    if (views.length && done === views.length) return '✓ Sincronización finalizada. No hay tareas pendientes. ' + done + ' de ' + views.length + ' conjuntos con última sincronización correcta. Última finalización: ' + latest + '.';
    return 'No hay tareas en curso. ' + done + ' de ' + views.length + ' conjuntos tienen una sincronización completada.';
}
if (typeof module !== 'undefined') module.exports = {kyberCurrentState: kyberCurrentState, kyberSyncSummary: kyberSyncSummary};
