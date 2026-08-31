"""Promote one verified release to test first, then production, with rollback.

Usage: python3.12 deploy/promote_release.py test|production /opt/kyber/release-<sha>
Credentials are never read or emitted by this script. Existing EnvironmentFile
is preserved. No sync worker, timer, database migration, or email is invoked.
"""
import datetime
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

mode, source_arg = sys.argv[1:]
assert mode in ('test', 'production')
production = mode == 'production'
source = Path(source_arg).resolve()
assert source.parent == Path('/opt/kyber') and source.name.startswith('release-')
revision = (source/'REVISION').read_text().strip()
assert len(revision) == 40 and all(c in '0123456789abcdef' for c in revision)
app = Path('/opt/kyber/app' if production else '/opt/kyber/app-pruebas')
app_unit = 'kyber-app' if production else 'kyber-app-pruebas'
panel_unit = 'kyber-panel' if production else 'kyber-panel-pruebas'
state = '/var/lib/' + panel_unit
envfile = '/etc/kyber/kyber.env' if production else '/etc/kyber/kyber-pruebas.env'
database = 'conepasa_auth' if production else 'conepasa_auth_pruebas'
app_path = '/' if production else '/pruebas/'
panel_url = 'https://kyber.com.py' + app_path + 'panel-sync'
stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
backup = Path('/opt/kyber/deploy')/('promotion-' + mode + '-' + stamp)
old = Path(str(app) + '.rollback-' + stamp)
new = Path(str(app) + '.next-' + stamp)
unit_dir = Path('/etc/systemd/system')
nginx = Path('/etc/nginx/conf.d/kyber.conf')
pool = Path('/etc/kyber/panel-fpm.conf' if production else '/etc/kyber/panel-pruebas-fpm.conf')
dropin = unit_dir/(app_unit + '.service.d/panel.conf')
panel_service = unit_dir/(panel_unit + '.service')
locations = Path('/etc/nginx/kyber-panel-locations.conf')

def run(*args):
    r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if r.returncode:
        # Do not relay service logs or diagnostics which may contain private data.
        raise RuntimeError('Command failed: ' + args[0] + ' ' + args[1])
    return r.stdout.strip()

def active(unit):
    return run('systemctl','show',unit,'-p','ActiveState','--value')

def hashfile(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

assert active('kyber-brief.service') == 'inactive', 'daily brief must not be running during code swap'
if production:
    assert (Path('/opt/kyber/app-pruebas')/'REVISION').read_text().strip() == revision
    assert hashfile(app/'app.py') == '09221fc05dc171f99b128fd3a9a51bff075ab07d4d237dd11a53fbdb178abfa9'
    assert hashfile(nginx) == 'b884eb389ed2c1941116799b7baa6e6b251596b85c70a7ad964f6c4c91d8382a'
    assert not locations.exists() and not panel_service.exists() and not dropin.exists()
else:
    assert (app/'REVISION').read_text().strip() == '871a80cf4bbb26126b549c1041ca0615c62129c2'
assert not old.exists() and not new.exists() and not backup.exists()
firebird_pid = run('systemctl','show','firebird.service','-p','MainPID','--value')
protected = [Path('/etc/kyber/kyber.env'), Path('/etc/kyber/kyber-pruebas.env')]
protected += list(unit_dir.glob('kyber-sync@.*')) + list(unit_dir.glob('kyber-brief.*'))
protected_hashes = {str(p):hashfile(p) for p in protected if p.is_file()}
backup.mkdir(parents=True)
changed = {}
swapped = False
renamed_old = False
stopped = False

def install(path, text):
    path = Path(path)
    if str(path) not in changed:
        saved = backup/('file-' + str(len(changed))) if path.exists() else None
        if saved:
            shutil.copy2(path, saved)
        changed[str(path)] = str(saved) if saved else None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o644)

def health(url):
    for _ in range(15):
        try:
            run('curl','--noproxy','*','--resolve','kyber.com.py:443:127.0.0.1',
                '--max-time','4','-fsS','-o','/dev/null',url)
            return
        except RuntimeError:
            time.sleep(1)
    raise RuntimeError('Health check did not become ready')

try:
    if not production:
        # Prepare BOTH sets of request files before watchers run. Creating a
        # production marker under an active PathModified could request a sync.
        run('systemctl','stop','kyber-panel-request@ekaru.path','kyber-panel-request@ejapo.path')
    shutil.copytree(source, new)
    shutil.copy2(app/'.streamlit/config.toml', new/'.streamlit/config.toml')
    # No application env file should be bundled. Secrets stay in /etc/kyber.
    assert not (new/'.env').exists()
    roots = [state] if production else [state, '/var/lib/kyber-panel']
    for root in roots:
        for name in ('tickets','leases','sessions','requests'):
            directory = Path(root)/name
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(0o700)
            run('chown','kyber:kyber',str(directory))
        Path(root).chmod(0o700)
        run('chown','kyber:kyber',root)
        for company in ('ekaru','ejapo'):
            marker = Path(root)/'requests'/company
            if not marker.exists():
                assert not production, 'request markers must be prepared before enabling watchers'
                marker.touch(mode=0o600)
                run('chown','kyber:kyber',str(marker))
    config = (source/'deploy/panel-pruebas/php-fpm.conf').read_text()
    service = (source/'deploy/panel-pruebas/kyber-panel-pruebas.service').read_text()
    if production:
        config = config.replace('kyber-panel-pruebas','kyber-panel').replace('/opt/kyber/app-pruebas','/opt/kyber/app')
        config = config.replace('/pruebas/panel-sync','/panel-sync').replace('= /pruebas/','= /')
        config = config.replace('conepasa_auth_pruebas','conepasa_auth').replace('env[KYBER_PANEL_ENV] = test','env[KYBER_PANEL_ENV] = production')
        service = service.replace('kyber-panel-pruebas','kyber-panel').replace('kyber-pruebas.env','kyber.env').replace('panel-pruebas-fpm.conf','panel-fpm.conf').replace('PRUEBAS','PRODUCCION')
        install(dropin, '[Service]\nEnvironment=KYBER_PANEL_STATE=' + state + '\nEnvironment=KYBER_PANEL_URL=' + panel_url + '\nReadWritePaths=' + state + '\n')
        block = (source/'deploy/panel-pruebas/nginx-locations.conf').read_text()
        # Only PHP allowlist and denial, never a second location / proxy.
        block = block[block.index('location ~ ^/pruebas/panel-sync/'):block.index('location /pruebas/ {')]
        block = block.replace('/pruebas/panel-sync','/panel-sync').replace('/opt/kyber/app-pruebas','/opt/kyber/app').replace('kyber-panel-pruebas','kyber-panel')
        install(locations, block)
        before = nginx.read_text()
        anchor = '    include /etc/nginx/kyber-pruebas-locations.conf;'
        assert before.count(anchor) == 2
        install(nginx, before.replace(anchor, anchor + '\n    include /etc/nginx/kyber-panel-locations.conf;', 1))
    else:
        # Stop only the request listeners; actual workers and their timers stay untouched.
        run('systemctl','stop','kyber-panel-request@ekaru.path','kyber-panel-request@ejapo.path')
        for suffix in ('service','path'):
            filename = 'kyber-panel-request@.' + suffix
            install(unit_dir/filename, (source/'deploy/panel-pruebas'/filename).read_text())
    install(pool, config)
    install(panel_service, service)
    run('nginx','-t')
    run('systemctl','stop',app_unit)
    stopped = True
    app.rename(old)
    renamed_old = True
    new.rename(app)
    swapped = True
    # Keep audit/history/logs live across promotion AND rollback; no copying old
    # snapshots back over new user writes. Retain the referenced rollback folder.
    for name in ('data','logs'):
        if (old/name).exists():
            assert not (app/name).exists()
            (app/name).symlink_to((old/name).resolve(), target_is_directory=True)
    run('chown','-R','--no-dereference','kyber:kyber',str(app))
    run('systemctl','daemon-reload')
    run('systemctl','restart',panel_unit)
    run('systemctl','start',app_unit)
    run('systemctl','reload','nginx')
    health('https://kyber.com.py' + app_path + '_stcore/health')
    health('https://kyber.com.py/pruebas/_stcore/health')
    health('https://kyber.com.py/_stcore/health')
    assert active(app_unit) == 'active' and active(panel_unit) == 'active'
    check = run('systemd-run','--unit=kyber-panel-verify-'+mode,'--wait','--pipe','--collect',
                '-p','User=kyber','-p','EnvironmentFile='+envfile,'-p','WorkingDirectory='+str(app),
                '--setenv=KYBER_PANEL_STATE='+state,'--setenv=KYBER_PANEL_URL='+panel_url,
                '--setenv=KYBER_PANEL_AUTH_DATABASE='+database,'--setenv=KYBER_PANEL_APP_PATH='+app_path,
                '/opt/kyber/venv/bin/python',str(app/'scripts/verify_panel_staging.py'))
    print(check)
    assert run('systemctl','show','firebird.service','-p','MainPID','--value') == firebird_pid
    assert all(hashfile(p) == digest for p,digest in protected_hashes.items())
    if not production:
        run('systemctl','reset-failed','kyber-panel-request@ekaru.service','kyber-panel-request@ejapo.service',
            'kyber-panel-request@ekaru.path','kyber-panel-request@ejapo.path')
        run('systemctl','start','kyber-panel-request@ekaru.path','kyber-panel-request@ejapo.path')
    run('systemctl','enable',panel_unit)
    print('DEPLOYED '+mode+' revision='+revision+' rollback='+str(old)+' backup='+str(backup))
except Exception as exc:
    print('DEPLOYMENT_FAILED: '+type(exc).__name__+'; restoring previous release', flush=True)
    subprocess.run(['systemctl','stop',panel_unit,app_unit], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for target,saved in changed.items():
        if saved:
            shutil.copy2(saved,target)
        elif Path(target).exists():
            Path(target).rename(backup/('new-'+Path(target).name))
    if renamed_old:
        if swapped:
            app.rename(backup/'failed-release')
        old.rename(app)
    run('systemctl','daemon-reload')
    run('nginx','-t')
    run('systemctl','reload','nginx')
    run('systemctl','start',app_unit)
    if not production:
        run('systemctl','start',panel_unit)
    print('ROLLED_BACK backup='+str(backup))
    sys.exit(1)
