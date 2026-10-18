#!/bin/sh
# Upgrade a working RustDesk Panel in place with fresh OSS online status.
# Only panel web/status services are recreated. HBBS/HBBR are never touched.
set -eu
umask 077
ROOT="${PANEL_DIR:-/volume3/docker/rustdesk-panel}"
BACKUP_ROOT="${PANEL_BACKUP_DIR:-/volume2/dsm-recovery}"
BASE="https://raw.githubusercontent.com/kostenkodm/rustdesk-panel/main"
STAGE=""
BACKUP=""

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
cleanup() { if [ -n "$STAGE" ]; then rm -rf "$STAGE"; fi; }
trap cleanup 0
trap 'exit 130' 2
trap 'exit 143' 15

health() {
    docker exec rustdesk-panel-web python -B -c \
      "import http.client; c=http.client.HTTPConnection('127.0.0.1',8765,timeout=3); c.request('GET','/healthz'); r=c.getresponse(); assert r.status==200 and r.read()==b'OK'" \
      >/dev/null 2>&1
}

snapshot_ok() {
    docker exec rustdesk-panel-web python -B -c \
      "import json,time; p='/peers/statuses.json'; d=json.load(open(p)); assert d.get('ok') is True and time.time()-d['timestamp']<65; assert isinstance(d['devices'],dict) and len(d['devices'])>0" \
      >/dev/null 2>&1
}

wait_for_services() {
    i=0
    while [ "$i" -lt 20 ]; do
        if health && docker exec rustdesk-panel-web grep -q 'def read_statuses' /app/web.py && snapshot_ok; then
            return 0
        fi
        i=$((i+1))
        sleep 2
    done
    return 1
}

rollback() {
    echo "==> Ошибка проверки. Откат только панели."
    docker compose -f compose.yaml ps || true
    docker logs --tail 30 rustdesk-panel-web 2>&1 || true
    docker logs --tail 30 rustdesk-panel-status 2>&1 || true
    docker compose -f compose.yaml stop web status 2>/dev/null || true
    # Undo new status container before reloading old Compose definition.
    docker rm -f rustdesk-panel-status >/dev/null 2>&1 || true
    cat "$BACKUP/web.py" > web.py || die "Не удалось восстановить web.py"
    cp "$BACKUP/compose.yaml" compose.yaml || die "Не удалось восстановить compose.yaml"
    if [ -f "$BACKUP/status_probe.py" ]; then
        cp "$BACKUP/status_probe.py" status_probe.py
    fi
    if [ -f "$BACKUP/status_worker.py" ]; then
        cp "$BACKUP/status_worker.py" status_worker.py
    else
        rm -f status_worker.py
    fi
    chmod 644 web.py
    docker compose -f compose.yaml up -d --no-deps --force-recreate web || die "Код восстановлен, но web не стартовал"
    sleep 3
    if health; then
        echo "ROLLBACK OK: старая панель снова отвечает /healthz"
    else
        echo "WARNING: код восстановлен, но старая панель не прошла health-check"
        docker logs --tail 30 rustdesk-panel-web 2>&1 || true
    fi
    echo "Резервная копия: $BACKUP"
    exit 1
}

command -v wget >/dev/null 2>&1 || die "wget отсутствует"
command -v docker >/dev/null 2>&1 || die "docker отсутствует"
[ -f "$ROOT/compose.yaml" ] && [ -f "$ROOT/web.py" ] && [ -f "$ROOT/.env" ] && [ -f "$ROOT/state/manager.sqlite3" ] \
    || die "Рабочая панель или .env не найдены"
cd "$ROOT"
STAGE="$(mktemp -d /tmp/rustdesk-panel-online.XXXXXX)" || die "Не удалось создать временный каталог"

echo "==> Скачиваем и проверяем файлы (действующие контейнеры пока не трогаем)"
for name in web.py compose.yaml status_probe.py status_worker.py; do
    wget -q -O "$STAGE/$name" "$BASE/$name" || die "Не скачан $name"
    [ -s "$STAGE/$name" ] || die "Пустой $name"
done
grep -q 'def read_statuses' "$STAGE/web.py" || die "Неправильная версия web.py"
grep -q '  status:' "$STAGE/compose.yaml" || die "В Compose отсутствует сервис status"
docker run --rm --network none -v "$STAGE:/src:ro" python:3.12-alpine python -B -c \
  "import ast,pathlib; [ast.parse((pathlib.Path('/src')/n).read_text()) for n in ('web.py','status_probe.py','status_worker.py')]" \
  || die "Ошибка синтаксиса Python"
docker compose --env-file "$ROOT/.env" -f "$STAGE/compose.yaml" config -q || die "Ошибка Compose"

echo "==> Останавливаем только RustDesk Panel web для резервной копии"
docker compose -f compose.yaml stop web || die "Не удалось остановить web"
BACKUP="$BACKUP_ROOT/rustdesk-panel-online-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$BACKUP" || { docker compose -f compose.yaml start web || true; die "Ошибка резервного каталога"; }
cp -p web.py compose.yaml "$BACKUP/" || { docker compose -f compose.yaml start web || true; die "Ошибка резервного копирования"; }
[ ! -f status_probe.py ] || cp -p status_probe.py "$BACKUP/"
[ ! -f status_worker.py ] || cp -p status_worker.py "$BACKUP/"
cp -a state "$BACKUP/state" || { docker compose -f compose.yaml start web || true; die "Ошибка резервного копирования базы"; }
echo "Резервная копия: $BACKUP"

echo "==> Устанавливаем код и конфигурацию панели"
# Keep bind-mounted web.py inode intact; other new files will be freshly mounted.
if ! cat "$STAGE/web.py" > web.py; then rollback; fi
chmod 644 web.py
cp "$STAGE/status_probe.py" status_probe.py || rollback
cp "$STAGE/status_worker.py" status_worker.py || rollback
chmod 644 status_probe.py status_worker.py
cp "$STAGE/compose.yaml" compose.yaml || rollback

echo "==> Пересоздаём только panel-web и запускаем panel-status"
docker compose -f compose.yaml up -d --no-deps --force-recreate web status || true

if wait_for_services; then
    echo "SUCCESS: RustDesk Panel онлайн-статусы доступны."
    echo "Резервная копия: $BACKUP"
    docker compose -f compose.yaml ps
    docker logs --tail 3 rustdesk-panel-status
    exit 0
fi
rollback
