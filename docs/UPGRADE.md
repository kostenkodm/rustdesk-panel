# Обновление установленной RustDesk Panel 0.1 → 0.2

Обновляем только **собственную веб-панель**, не трогая контейнеры HBBS/HBBR, их ключи и SQLite.

## Что нового в 0.2

- Избранные устройства (звёздочка на карточке).
- Фильтр по группе и избранному, поиск.
- Массовое назначение группы отмеченным устройствам.
- Скачать CSV со списком устройств, названиями, группами, заметками и ОС.
- Автоматическое добавление колонки favorite в существующую базу панели, без очистки карточек и заметок.

## Быстрое обновление одной командой

В SSH под `root`:

```sh
wget -q -O /tmp/rustdesk-panel-upgrade.sh https://raw.githubusercontent.com/kostenkodm/rustdesk-panel/main/scripts/upgrade.sh && sh /tmp/rustdesk-panel-upgrade.sh
```

Скрипт сначала скачает и проверит Python-код, затем **остановит только веб-панель**, создаст резервную копию базы в `/volume2/dsm-recovery`, запустит обновление и проверит HTTP health-check. Если проверка не пройдёт, он автоматически вернёт предыдущий `web.py`. `HBBS` и `HBBR` не затрагиваются.

Ручной способ ниже оставлен для диагностики и восстановления.

## Обновление уже работающего NAS

Под root, по SSH:

1. Проверить, что текущая панель находится в `/volume3/docker/rustdesk-panel` и содержит `compose.yaml`, `web.py`, `state/manager.sqlite3`.
2. Остановить **только** контейнер web, чтобы сохранить согласованную резервную копию SQLite.
3. Сделать резервную копию состояния и файла Python за пределами каталога проекта.
4. Скачать новую версию `web.py` из GitHub во временный файл. Проверить, что это действительно Python и файл не пустой.
5. Заменить исходник, запустить web, проверить журнал и вход.

Команды (выполнять последовательно):

```sh
sudo -i
cd /volume3/docker/rustdesk-panel

test -f web.py && test -d state && test -f compose.yaml || exit 1

docker compose -f compose.yaml stop web

BACKUP="/volume2/dsm-recovery/rustdesk-panel-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP"
cp -p web.py "$BACKUP/web.py"
cp -a state "$BACKUP/state"

wget -O web.py.next https://raw.githubusercontent.com/kostenkodm/rustdesk-panel/main/web.py

test -s web.py.next || { echo "STOP: новая версия не скачана"; exit 1; }

# Проверка синтаксиса без записи pycache в каталог проекта.
docker run --rm --network none -v "$PWD/web.py.next:/src/web.py:ro" python:3.12-alpine python -B -c "import ast; ast.parse(open('/src/web.py', encoding='utf-8').read())"

chmod 644 web.py.next
mv web.py.next web.py
docker compose -f compose.yaml up -d web

docker compose -f compose.yaml ps
docker logs --tail 30 rustdesk-panel-web
```

**Не запускать `wget` из ветки `main` до того, как PR будет объединён и проверен.**

Откройте панель из домашней сети / VPN: http://192.168.30.150:8765

## Откат

Если после обновления панель не запускается:

```sh
cd /volume3/docker/rustdesk-panel
docker compose -f compose.yaml stop web

# Вместо указанного каталога укажите фактически выведенный при создании резервной копии.
BACKUP="/volume2/dsm-recovery/rustdesk-panel-YYYYMMDD-HHMMSS"
cp -p "$BACKUP/web.py" web.py
chmod 644 web.py
# Состояние базы после миграции 0.2, скорее всего, работает с 0.1, но
# при необходимости восстановления именно прежнего состояния:
# mv state "state-after-failed-upgrade-$(date +%Y%m%d-%H%M%S)"
# cp -a "$BACKUP/state" state

docker compose -f compose.yaml up -d web
```

HBBS/HBBR не затрагиваются при обновлении и откате.

## Ограничения

- Наличие устройства в HBBS SQLite — не доказательство, что устройство сейчас онлайн.
- Импортируются только доступные и безопасно отобранные поля; база HBBS открывается только на чтение.
- CSV может содержать реальные имена компьютеров и заметки: храните выгрузку приватно.
- HTTP-панель предназначена только для доверенной LAN/VPN.
