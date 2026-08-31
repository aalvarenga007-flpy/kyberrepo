"""Exercise the real systemd path/service scheduling with a harmless stub.

Only test-owned units under /run are created; no Kyber worker is invoked.
Uses the release's service definition unchanged except its ExecStart target.
"""
import pathlib
import subprocess
import tempfile
import time

root = pathlib.Path(__file__).resolve().parents[1]
scratch = pathlib.Path(tempfile.mkdtemp(prefix='kyber-wake-check-', dir='/run'))
name = scratch.name
unit_root = pathlib.Path('/run/systemd/system')
service = unit_root / (name + '.service')
watch = unit_root / (name + '.path')

def run(*args):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout.strip()

def count():
    return len((scratch / 'starts').read_text().splitlines()) if (scratch / 'starts').exists() else 0

def idle():
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        state = run('systemctl', 'show', name + '.service', '-p', 'ActiveState', '--value')
        if state == 'inactive':
            return
        assert state != 'failed', 'burst triggered a failed service'
        time.sleep(.2)
    raise AssertionError('test dispatcher did not become idle')

try:
    for marker in ('test', 'production'):
        (scratch / marker).touch()
    # Only this harmless counter replaces the real worker start.
    stub = scratch / 'stub.sh'
    stub.write_text('#!/bin/sh\nprintf "start\\n" >> "' + str(scratch / 'starts') + '"\n')
    unit = (root / 'deploy/panel-pruebas/kyber-panel-request@.service').read_text()
    unit = unit.replace('ExecStart=/usr/bin/systemctl start --no-block kyber-sync@%i.service',
                        'ExecStart=/bin/sh ' + str(stub))
    unit += '\nReadWritePaths=' + str(scratch) + '\n'
    service.write_text(unit)
    watch.write_text('[Unit]\nDescription=Harmless Kyber burst check\n[Path]\n'
                     + ''.join('PathModified=' + str(scratch / m) + '\n' for m in ('test','production'))
                     + 'Unit=' + name + '.service\n')
    run('systemctl', 'daemon-reload')
    run('systemctl', 'start', name + '.path')
    for i in range(40):
        (scratch / ('test' if i % 2 else 'production')).write_text(str(i))
        time.sleep(.15)
    idle()
    first = count()
    assert 1 <= first <= 3, 'burst was not coalesced'
    assert run('systemctl', 'is-active', name + '.path') == 'active'
    # A later independent request must still start the stub.
    (scratch / 'production').write_text('later')
    deadline = time.monotonic() + 12
    while count() <= first and time.monotonic() < deadline:
        time.sleep(.2)
    assert count() > first, 'next request was lost'
    idle()
    assert run('systemctl', 'is-active', name + '.path') == 'active'
    print('BURST_OK: 40 requests coalesced; next independent request works; no real worker invoked')
finally:
    subprocess.run(['systemctl','stop',name+'.path',name+'.service'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    service.unlink(missing_ok=True)
    watch.unlink(missing_ok=True)
    subprocess.run(['systemctl','daemon-reload'], check=True)
    # Keep the tiny fixture/counter for diagnosis, never delete user data.
