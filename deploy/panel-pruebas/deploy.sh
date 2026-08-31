#!/usr/bin/env bash
# First-time installation. Invoked from an exact, verified Git release.
set -euo pipefail
release=$(realpath "${1:?release directory required}")
case "$release" in /opt/kyber/app-pruebas.release-*) ;; *) echo INVALID_RELEASE; exit 1;; esac
test -f "$release/sync/gateway.php"
test ! -e /etc/systemd/system/kyber-panel-pruebas.service
test ! -e /etc/systemd/system/kyber-app-pruebas.service.d/panel.conf
stamp=$(date +%Y%m%d-%H%M%S)
backup="/opt/kyber/deploy/panel-backup-$stamp"
mkdir -p "$backup"
cp -a /etc/nginx/conf.d/kyber-pruebas.conf "$backup/nginx.conf"
sha256sum /etc/nginx/conf.d/kyber.conf > "$backup/production.sha256"
old="/opt/kyber/app-pruebas.rollback-$stamp"
swapped=0
rollback() {
    set +e
    systemctl stop kyber-panel-request@ekaru.path kyber-panel-request@ejapo.path kyber-panel-pruebas.service
    cp -a "$backup/nginx.conf" /etc/nginx/conf.d/kyber-pruebas.conf
    if [ "$swapped" = 1 ]; then
        systemctl stop kyber-app-pruebas
        mv /opt/kyber/app-pruebas "$backup/failed-release"
        mv "$old" /opt/kyber/app-pruebas
    fi
    for file in /etc/systemd/system/kyber-app-pruebas.service.d/panel.conf /etc/systemd/system/kyber-panel-pruebas.service /etc/systemd/system/kyber-panel-request@.path /etc/systemd/system/kyber-panel-request@.service; do
        [ ! -f "$file" ] || mv "$file" "$backup/$(basename "$file")"
    done
    systemctl daemon-reload
    nginx -t && systemctl reload nginx
    systemctl restart kyber-app-pruebas
    echo "ROLLED_BACK backup=$backup"
}
trap rollback ERR
install -d -o kyber -g kyber -m 0700 /var/lib/kyber-panel-pruebas
for d in tickets leases sessions requests; do install -d -o kyber -g kyber -m 0700 "/var/lib/kyber-panel-pruebas/$d"; done
# Create request files BEFORE enabling watchers; installation must not sync.
for c in ekaru ejapo; do install -o kyber -g kyber -m 0600 /dev/null "/var/lib/kyber-panel-pruebas/requests/$c"; done
install -m 0644 "$release/deploy/panel-pruebas/php-fpm.conf" /etc/kyber/panel-pruebas-fpm.conf
for unit in kyber-panel-pruebas.service kyber-panel-request@.path kyber-panel-request@.service; do
    install -m 0644 "$release/deploy/panel-pruebas/$unit" "/etc/systemd/system/$unit"
done
mkdir -p /etc/systemd/system/kyber-app-pruebas.service.d
install -m 0644 "$release/deploy/panel-pruebas/app-panel.conf" /etc/systemd/system/kyber-app-pruebas.service.d/panel.conf
# Keep 9029 working while the new TLS route is tested externally.
cp "$release/deploy/panel-pruebas/nginx.conf" "$backup/final-nginx.conf"
python3 - "$backup/nginx.conf" "$release/deploy/panel-pruebas/nginx.conf" <<'PY'
import pathlib, sys
old = pathlib.Path(sys.argv[1]).read_text()
new = pathlib.Path(sys.argv[2]).read_text()
secure = new[new.index('server {\n    listen 8443'):]
pathlib.Path('/etc/nginx/conf.d/kyber-pruebas.conf').write_text(old + '\n' + secure)
PY
nginx -t
cp /opt/kyber/app-pruebas/.streamlit/config.toml "$release/.streamlit/config.toml"
chown -R kyber:kyber "$release"
mv /opt/kyber/app-pruebas "$old"
mv "$release" /opt/kyber/app-pruebas
swapped=1
systemctl daemon-reload
systemctl start kyber-panel-pruebas.service
systemctl restart kyber-app-pruebas.service
systemctl reload nginx
sleep 3
systemctl is-active --quiet kyber-panel-pruebas kyber-app-pruebas
curl --noproxy '*' --resolve kyber.com.py:8443:127.0.0.1 -fsS https://kyber.com.py:8443/healthz
sha256sum -c "$backup/production.sha256"
trap - ERR
echo "PREPARED backup=$backup rollback=$old"
echo '9029 unchanged until external HTTPS validation. Workers not started.'
