#!/bin/sh
# RustDesk Panel 0.4: upgrade web, Compose, accounts and address books.
# Leaves existing HBBS, HBBR, exporter, and status poller intact.
set -eu
umask 077

ROOT="${PANEL_DIR:-/volume3/docker/rustdesk-panel}"
BACKUP_ROOT="${PANEL_BACKUP_DIR:-/volume2/dsm-recovery}"
BASE="https://raw.githubusercontent.com/kostenkodm/rustdesk-panel/main"
STAGE=""
BACKUP=""

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
cleanup() { [ -z "$STAGE" ] || rm -rf "$STAGE"; }
trap cleanup 0
trap 'exit 130' 2
trap 'exit 143' 15

health() {
    docker exec rustdesk-panel-web python -B -c \
      "import http.client; c=http.client.HTTPConnection('127.0.0.1',8765,timeout=3); c.request('GET','/healthz'); r=c.getresponse(); assert r.status==200 and r.read()==b'OK'" \
      >/dev/null 2>&1
}
schema_ok() {
    docker exec rustdesk-panel-web python -B -c \
      "import os,sqlite3; p=os.path.join(os.getenv('STATE_DIR','/state'),'manager.sqlite3'); db=sqlite3.connect('file:'+p+'?mode=ro',uri=True); assert db.execute('select count(*) from users where username=? and role=? and active=1',('admin','admin')).fetchone()[0]>=1; assert db.execute('select count(*) from address_books').fetchone()[0]>=1" \
      >/dev/null 2>&1
}
wait_web() {
    i=0
    while [ "$i" -lt 15 ]; do
        if health && schema_ok && docker exec rustdesk-panel-web python -B -c 'import accounts,panel_pages' >/dev/null 2>&1; then
            return 0
        fi
        i=$((i+1))
        sleep 2
    done
    return 1
}
rollback() {
    echo "==> Сбой обновления. Диагностика новой версии:"
    docker compose -f compose.yaml ps || true
    docker logs --tail 35 rustdesk-panel-web 2>&1 || true
    docker compose -f compose.yaml stop web || true
    [ -f "$BACKUP/web.py" ] || die "Резервная копия web.py не найдена"
    # Restore the inode of already bind-mounted web.py; container is recreated below.
    cat "$BACKUP/web.py" > web.py || die "Не удалось восстановить web.py"
    cp "$BACKUP/compose.yaml" compose.yaml || die "Не удалось восстановить Compose"
    [ ! -f "$BACKUP/accounts.py" ] || cp "$BACKUP/accounts.py" accounts.py
    [ ! -f "$BACKUP/panel_pages.py" ] || cp "$BACKUP/panel_pages.py" panel_pages.py
    chmod 644 web.py compose.yaml
    # The schema migration adds a column to sessions. Restore the old DB too,
    # otherwise the older app cannot INSERT into sessions on login.
    if [ -d "$BACKUP/state" ]; then
        mv state "$BACKUP/state-after-failed-upgrade" || die "Не удалось сохранить сбойную базу"
        cp -a "$BACKUP/state" state || die "Не удалось восстановить базу"
    fi
    docker compose -f compose.yaml up -d --no-deps --force-recreate web \
      || die "Код и БД восстановлены, но web не стартовал"
    if wait_web_old; then
        echo "ROLLBACK OK: прежняя панель снова отвечает /healthz"
    else
        echo "WARNING: старые файлы восстановлены, но панель не прошла health-check"
        docker logs --tail 25 rustdesk-panel-web 2>&1 || true
    fi
    echo "Резервная копия: $BACKUP"
    exit 1
}
wait_web_old() {
    i=0
    while [ "$i" -lt 12 ]; do
        if health; then return 0; fi
        i=$((i+1))
        sleep 2
    done
    return 1
}

command -v docker >/dev/null 2>&1 || die "Docker отсутствует"
command -v wget >/dev/null 2>&1 || die "wget отсутствует"
[ -f "$ROOT/web.py" ] && [ -f "$ROOT/compose.yaml" ] && [ -f "$ROOT/.env" ] && [ -f "$ROOT/state/manager.sqlite3" ] \
  || die "Работающая панель не найдена: $ROOT"
cd "$ROOT"
STAGE="$(mktemp -d /tmp/rustdesk-panel-users.XXXXXX)" || die "Невозможно создать временную папку"

echo "==> Скачивание и проверка RustDesk Panel 0.4"
for file in web.py compose.yaml accounts.py panel_pages.py; do
    wget -q -O "$STAGE/$file" "$BASE/$file" || die "Не удалось скачать $file"
    [ -s "$STAGE/$file" ] || die "Пустой $file"
done
grep -q 'import panel_pages' "$STAGE/web.py" || die "Это не RustDesk Panel 0.4"
grep -q 'accounts.py:/app/accounts.py:ro' "$STAGE/compose.yaml" || die "Compose не содержит модуль учётных записей"
docker run --rm --network none -v "$STAGE:/source:ro" python:3.12-alpine python -B -c \
  "import ast,pathlib; [ast.parse((pathlib.Path('/source')/name).read_text('utf-8')) for name in ('web.py','accounts.py','panel_pages.py')]" \
  || die "Синтаксическая ошибка Python"
docker compose --env-file "$ROOT/.env" -f "$STAGE/compose.yaml" config -q || die "Некорректный Compose"

echo "==> Остановка только rustdesk-panel-web"
docker compose -f compose.yaml stop web || die "Не удалось остановить веб-панель"
BACKUP="$BACKUP_ROOT/rustdesk-panel-users-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$BACKUP" || { docker compose -f compose.yaml start web || true; die "Нет каталога резервной копии"; }
cp -p web.py compose.yaml "$BACKUP/" || { docker compose -f compose.yaml start web || true; die "Не удалось сохранить исходники"; }
for file in accounts.py panel_pages.py; do
    if [ -f "$file" ]; then
        cp -p "$file" "$BACKUP/$file" || die "Не удалось сохранить $file"
    fi
done
cp -a state "$BACKUP/state" || { docker compose -f compose.yaml start web || true; die "Не удалось сохранить базу"; }
echo "Резервная копия: $BACKUP"

echo "==> Обновление панели и добавление модулей"
cat "$STAGE/web.py" > web.py || rollback
cp "$STAGE/accounts.py" accounts.py || rollback
cp "$STAGE/panel_pages.py" panel_pages.py || rollback
cp "$STAGE/compose.yaml" compose.yaml || rollback
chmod 644 web.py accounts.py panel_pages.py compose.yaml

echo "==> Пересоздание только rustdesk-panel-web"
docker compose -f compose.yaml up -d --no-deps --force-recreate web || true

if wait_web; then
    echo "SUCCESS: RustDesk Panel 0.4, учётные записи и адресные книги активны."
    echo "Backup: $BACKUP"
    docker compose -f compose.yaml ps
    exit 0
fi
rollback
