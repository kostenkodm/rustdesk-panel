#!/bin/sh
# RustDesk Panel: safe web-only upgrade; preserve the existing bind-mounted inode.
# Does not reconfigure, stop or restart HBBS/HBBR.
set -eu
umask 077

ROOT="${PANEL_DIR:-/volume3/docker/rustdesk-panel}"
BACKUP_ROOT="${PANEL_BACKUP_DIR:-/volume2/dsm-recovery}"
URL="https://raw.githubusercontent.com/kostenkodm/rustdesk-panel/main/web.py"
TMP=""

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
cleanup() { [ -z "$TMP" ] || rm -f "$TMP"; }
trap cleanup 0
trap 'exit 130' 2
trap 'exit 143' 15

health() {
    docker exec rustdesk-panel-web python -B -c \
      "import http.client; c=http.client.HTTPConnection('127.0.0.1', 8765, timeout=3); c.request('GET','/healthz'); r=c.getresponse(); assert r.status==200 and r.read()==b'OK'" \
      >/dev/null 2>&1
}

wait_for_health() {
    attempt=0
    while [ "$attempt" -lt 15 ]; do
        if health; then return 0; fi
        attempt=$((attempt + 1))
        sleep 2
    done
    return 1
}

diagnostics() {
    echo "==> Контейнер и последние сообщения журнала:"
    docker compose -f compose.yaml ps || true
    docker logs --tail 35 rustdesk-panel-web 2>&1 || true
}

command -v docker >/dev/null 2>&1 || fail "docker не найден"
command -v wget >/dev/null 2>&1 || fail "wget не найден"
[ -f "$ROOT/compose.yaml" ] || fail "compose.yaml не найден: $ROOT"
[ -f "$ROOT/web.py" ] || fail "web.py не найден: $ROOT"
[ -f "$ROOT/state/manager.sqlite3" ] || fail "База панели отсутствует; откройте docs/INSTALL.md"
cd "$ROOT"

echo "==> Скачивание новой версии"
TMP="$(mktemp /tmp/rustdesk-panel-upgrade.XXXXXX)" || fail "mktemp"
wget -q -O "$TMP" "$URL" || fail "Не удалось скачать web.py; рабочая панель не затронута"
[ -s "$TMP" ] || fail "Скачанный файл пуст"
grep -q 'def export_inventory_csv' "$TMP" || fail "Не найдены функции RustDesk Panel 0.2"

echo "==> Проверка синтаксиса"
docker run --rm --network none -v "$TMP:/src/web.py:ro" python:3.12-alpine \
  python -B -c "import ast; ast.parse(open('/src/web.py', encoding='utf-8').read())" \
  || fail "Синтаксическая ошибка; рабочая панель не затронута"

echo "==> Останавливаем только веб-контейнер"
docker compose -f compose.yaml stop web || fail "Не удалось остановить веб-панель"

BACKUP="$BACKUP_ROOT/rustdesk-panel-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$BACKUP" || { docker compose -f compose.yaml start web || true; fail "Не удалось создать каталог резервной копии"; }
cp -p web.py "$BACKUP/web.py" || { docker compose -f compose.yaml start web || true; fail "Не удалось сохранить web.py"; }
cp -a state "$BACKUP/state" || { docker compose -f compose.yaml start web || true; fail "Не удалось сохранить базу панели"; }
echo "Резервная копия: $BACKUP"

# CRITICAL: web.py is a Docker bind-mounted file.
# Do not replace it using mv/rename: preserve its inode and mount reference.
if ! cat "$TMP" > web.py; then
    cat "$BACKUP/web.py" > web.py || true
    docker compose -f compose.yaml start web || true
    fail "Не удалось записать web.py"
fi
chmod 644 web.py

echo "==> Запуск RustDesk Panel 0.2"
# Force new container to rebind /app/web.py to the current host inode.
# A previous update used 'mv', so 'start' can retain a stale inode forever.
docker compose -f compose.yaml up -d --no-deps --force-recreate web || true

if wait_for_health && docker exec rustdesk-panel-web grep -q 'def export_inventory_csv' /app/web.py; then
    echo "SUCCESS: RustDesk Panel 0.2 работает, HBBS/HBBR не затронуты."
    echo "Backup: $BACKUP"
    docker compose -f compose.yaml ps
    exit 0
fi

echo "==> Проверка не пройдена. Сохраняем диагностику и выполняем откат."
diagnostics
docker compose -f compose.yaml stop web || true
cat "$BACKUP/web.py" > web.py || fail "Не удалось восстановить web.py из $BACKUP"
chmod 644 web.py
docker compose -f compose.yaml up -d --no-deps --force-recreate web || fail "Старый web.py восстановлен, но контейнер не запустился"

if wait_for_health; then
    echo "ROLLBACK OK: прежняя панель снова отвечает /healthz"
else
    echo "WARNING: исходный код восстановлен, но веб-сервис не прошёл health-check"
    diagnostics
fi
echo "Backup: $BACKUP"
exit 1
