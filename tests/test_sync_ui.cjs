const assert = require('node:assert/strict');
const {kyberCurrentState, kyberSyncSummary} = require('../sync/ui_status.js');
const done = {last_sync_status:'ok', last_sync_end:'2026-08-31 12:39:13', queue_job:null};
assert.match(kyberSyncSummary([done, done]), /finalizada.*2 de 2/);
assert.match(kyberCurrentState(done), /completada: 2026-08-31 12:39:13/);
for (const status of ['running', 'pending', 'paused']) {
    const view = {...done, queue_job:{status}};
    assert.doesNotMatch(kyberSyncSummary([done, view]), /finalizada/);
    assert.doesNotMatch(kyberCurrentState(view), /completada/);
}
assert.match(kyberSyncSummary([{...done,last_sync_status:'error'}]), /Requiere atención/);
assert.doesNotMatch(kyberSyncSummary([]), /finalizada/);
assert.doesNotMatch(kyberSyncSummary([{last_sync_status:'never'}]), /finalizada/);
console.log('Sync UI: 11 assertions passed');
