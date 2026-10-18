#!/bin/sh
# RustDesk Panel web-only upgrade from the public main branch.
# Intentionally does not touch HBBS/HBBR, RustDesk keys or Compose configuration.
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

command -v docker >/dev/null 2>&1 || fail "docker не найден"
command -v wget >/dev/null 2>&1 || fail "wget не найден"
[ -f "$ROOT/compose.yaml" ] || fail "compose.yaml не найден в $ROOT"
[ -f "$ROOT/web.py" ] || fail "web.py не найден в $ROOT"
[ -f "$ROOT/state/manager.sqlite3" ] || fail "База панели не найдена. Не обновляем незавершённую установку."
cd "$ROOT"

echo "==> Скачивание новой версии web.py"
TMP="$(mktemp /tmp/rustdesk-panel-web.XXXXXX)" || fail "mktemp"
wget -q -O "$TMP" "$URL" || fail "Не удалось скачать web.py; действующая версия не затронута"
[ -s "$TMP" ] || fail "Скачанный файл пустой"
grep -q "def export_inventory_csv" "$TMP" || fail "Файл не похож на RustDesk Panel 0.2"

echo "==> Проверка синтаксиса Python"
docker run --rm --network none -v "$TMP:/src/web.py:ro" python:3.12-alpine \
  python -B -c "import ast; ast.parse(open('/src/web.py', encoding='utf-8').read())" \
  || fail "Ошибка синтаксиса, действующая версия не затронута"

echo "==> Остановка только rustdesk-panel-web"
docker compose -f compose.yaml stop web || fail "Не удалось остановить web"

BACKUP="$BACKUP_ROOT/rustdesk-panel-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$BACKUP" || fail "Не удалось создать резервный каталог. Перезапусти web."
cp -p web.py "$BACKUP/web.py" || fail "Не удалось сохранить web.py. Перезапусти web."
cp -a state "$BACKUP/state" || fail "Не удалось сохранить SQLite. Перезапусти web."
echo "Резервная копия: $BACKUP"

# Move only the source file. No .env, keys, state, compose or HBBS paths changed.
cp "$TMP" web.py.next || fail "Не удалось подготовить web.py.next. Перезапусти web."
chmod 644 web.py.next
mv web.py.next web.py

echo "==> Запуск новой версии панели"
docker compose -f compose.yaml up -d web || true

i=0
while [ "$i" -lt 12 ]; do
    if docker exec rustdesk-panel-web python -B -c \
      "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=2).read() == b'OK'" \
      >/dev/null 2>&1; then
        echo "SUCCESS: RustDesk Panel обновлена до версии 0.2."
        echo "Backup: $BACKUP"
        docker compose -f compose.yaml ps
        exit 0
    fi
    i=$((i+1))
    sleep 2
done

echo "==> Новая версия не прошла health-check. Возвращаем прежний web.py."
docker compose -f compose.yaml stop web || true
cp -p "$BACKUP/web.py" web.py || fail "Не удалось восстановить web.py автоматически"
chmod 644 web.py
docker compose -f compose.yaml up -d web || fail "Откат выполнен, но старый web не запускается. Проверьте журналы."
echo "ERROR: health-check failed; restored previous web.py."
echo "Backup: $BACKUP"
exit 1
