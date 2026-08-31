#!/usr/bin/env bash
# One-time migration of the inspected 9029 test app to HTTPS /pruebas/ (v1.0.3).
# No production release, SQL migration, real synchronization, or firewall change.
set -Eeuo pipefail
release=$(realpath "${1:?release directory required}")
case "$release" in /opt/kyber/app-pruebas.release-*) ;; *) echo INVALID_RELEASE; exit 1;; esac
test -f "$release/sync/gateway.php"
test -f "$release/REVISION"
test "$(cat /opt/kyber/app-pruebas/REVISION)" = e7cd0b2275f7676a0026d459f033ff9fb6a7592d
test "$(sha256sum /etc/nginx/conf.d/kyber.conf | cut -d' ' -f1)" = f314c1ac8127d9179f1984b192b320a762c1b8503a5375cefac57a9acdb8a2cd
test ! -e /etc/nginx/kyber-pruebas-locations.conf
test ! -e /etc/systemd/system/kyber-app-pruebas.service.d/panel.conf
for unit in kyber-panel-pruebas.service kyber-panel-request@ekaru.path kyber-panel-request@ejapo.path; do
    ! systemctl is-active --quiet "$unit"
    ! systemctl is-enabled --quiet "$unit"
done
prod_pid=$(systemctl show kyber-app.service -p MainPID --value)
firebird_pid=$(systemctl show firebird.service -p MainPID --value)
stamp=$(date +%Y%m%d-%H%M%S)
backup="/opt/kyber/deploy/panel-https-$stamp"
old="/opt/kyber/app-pruebas.rollback-$stamp"
test ! -e "$backup" && test ! -e "$old"
mkdir -p "$backup"
cp -a /etc/nginx/conf.d/kyber.conf "$backup/kyber.conf"
cp -a /etc/nginx/conf.d/kyber-pruebas.conf "$backup/kyber-pruebas.conf"
cp -a /etc/kyber/panel-pruebas-fpm.conf "$backup/php-fpm.conf"
for unit in kyber-panel-pruebas.service kyber-panel-request@.path kyber-panel-request@.service; do
    cp -a "/etc/systemd/system/$unit" "$backup/$unit"
done
find /opt/kyber/app -type f ! -path '*/__pycache__/*' ! -name '*.pyc' -name '*.py' -print0 | sort -z | xargs -0 sha256sum > "$backup/production-code.sha256"
swapped=0
rollback() {
    trap - ERR
    set +e
    systemctl stop kyber-panel-pruebas.service kyber-app-pruebas.service
    cp -a "$backup/kyber.conf" /etc/nginx/conf.d/kyber.conf
    cp -a "$backup/kyber-pruebas.conf" /etc/nginx/conf.d/kyber-pruebas.conf
    cp -a "$backup/php-fpm.conf" /etc/kyber/panel-pruebas-fpm.conf
    for unit in kyber-panel-pruebas.service kyber-panel-request@.path kyber-panel-request@.service; do
        cp -a "$backup/$unit" "/etc/systemd/system/$unit"
    done
    if [ -f /etc/systemd/system/kyber-app-pruebas.service.d/panel.conf ]; then
        mv /etc/systemd/system/kyber-app-pruebas.service.d/panel.conf "$backup/failed-app-panel.conf"
    fi
    if [ -f /etc/nginx/kyber-pruebas-locations.conf ]; then
        mv /etc/nginx/kyber-pruebas-locations.conf "$backup/failed-nginx-locations.conf"
    fi
    if [ "$swapped" = 1 ]; then
        mv /opt/kyber/app-pruebas "$backup/failed-release"
        mv "$old" /opt/kyber/app-pruebas
    fi
    systemctl daemon-reload
    nginx -t && systemctl reload nginx
    systemctl start kyber-app-pruebas.service
    echo "ROLLED_BACK backup=$backup"
}
trap rollback ERR
install -d -o kyber -g kyber -m 0700 /var/lib/kyber-panel-pruebas
for d in tickets leases sessions requests; do
    install -d -o kyber -g kyber -m 0700 "/var/lib/kyber-panel-pruebas/$d"
done
# Do not modify existing request files: installation must not request a sync.
for c in ekaru ejapo; do
    test -f "/var/lib/kyber-panel-pruebas/requests/$c" || install -o kyber -g kyber -m 0600 /dev/null "/var/lib/kyber-panel-pruebas/requests/$c"
done
install -m 0644 "$release/deploy/panel-pruebas/php-fpm.conf" /etc/kyber/panel-pruebas-fpm.conf
for unit in kyber-panel-pruebas.service kyber-panel-request@.path kyber-panel-request@.service; do
    install -m 0644 "$release/deploy/panel-pruebas/$unit" "/etc/systemd/system/$unit"
done
mkdir -p /etc/systemd/system/kyber-app-pruebas.service.d
install -m 0644 "$release/deploy/panel-pruebas/app-panel.conf" /etc/systemd/system/kyber-app-pruebas.service.d/panel.conf
install -m 0644 "$release/deploy/panel-pruebas/nginx-locations.conf" /etc/nginx/kyber-pruebas-locations.conf
# Preflight hash above pins the inspected file. Only add one scoped include.
sed -i '/^    server_name kyber.com.py www.kyber.com.py;$/a\    include /etc/nginx/kyber-pruebas-locations.conf;' /etc/nginx/conf.d/kyber.conf
install -m 0644 "$release/deploy/panel-pruebas/nginx.conf" /etc/nginx/conf.d/kyber-pruebas.conf
nginx -t
cp /opt/kyber/app-pruebas/.streamlit/config.toml "$release/.streamlit/config.toml"
chown -R kyber:kyber "$release"
systemctl stop kyber-app-pruebas.service
mv /opt/kyber/app-pruebas "$old"
mv "$release" /opt/kyber/app-pruebas
swapped=1
systemctl daemon-reload
systemctl start kyber-panel-pruebas.service kyber-app-pruebas.service
systemctl reload nginx
curl --retry 8 --retry-connrefused --retry-delay 1 --max-time 10 -fsS http://127.0.0.1:8503/pruebas/_stcore/health
systemctl is-active --quiet kyber-panel-pruebas.service
systemctl is-active --quiet kyber-app-pruebas.service
curl --noproxy '*' --resolve kyber.com.py:443:127.0.0.1 --max-time 15 -fsS https://kyber.com.py/pruebas/healthz
curl --noproxy '*' --resolve kyber.com.py:443:127.0.0.1 --max-time 15 -fsS https://kyber.com.py/healthz
test "$(systemctl show kyber-app.service -p MainPID --value)" = "$prod_pid"
test "$(systemctl show firebird.service -p MainPID --value)" = "$firebird_pid"
sha256sum -c "$backup/production-code.sha256" > "$backup/production-verified.txt"
# Removing exactly the added include must recover the original vhost byte-for-byte.
sed '\|^    include /etc/nginx/kyber-pruebas-locations.conf;$|d' /etc/nginx/conf.d/kyber.conf | cmp -s - "$backup/kyber.conf"
systemd-run --unit=kyber-panel-readonly-check --wait --pipe --collect \
    -p User=kyber -p EnvironmentFile=/etc/kyber/kyber-pruebas.env \
    -p WorkingDirectory=/opt/kyber/app-pruebas \
    --setenv=KYBER_PANEL_STATE=/var/lib/kyber-panel-pruebas \
    --setenv=KYBER_PANEL_URL=https://kyber.com.py/pruebas/panel-sync \
    /opt/kyber/venv/bin/python /opt/kyber/app-pruebas/scripts/verify_panel_staging.py
trap - ERR
echo "PREPARED backup=$backup rollback=$old"
echo 'Validate external HTTPS before enabling panel service and the two path watchers. No synchronization was requested.'
