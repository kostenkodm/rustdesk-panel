# RustDesk Panel 0.3: настоящие онлайн/офлайн статусы

В этом обновлении создаётся новый контейнер `rustdesk-panel-status` для штатного запроса `OnlineRequest` к HBBS TCP 21115. Он опрашивает сервер раз в 20 секунд. Результат записывается в `shared/statuses.json`; существующая панель читает этот файл при открытии страницы. Отсутствие свежего ответа означает `Нет данных`.

Ранее проверено на работающем HBBS: `total=23 online=5 offline=18`. Значения динамические и могут измениться.

## Установка без переустановки HBBS/HBBR

Запускайте в SSH Synology под root. Рабочий каталог — `/volume3/docker/rustdesk-panel`.

```sh
cd /volume3/docker/rustdesk-panel
wget -O /tmp/rustdesk-panel-online-upgrade.sh https://raw.githubusercontent.com/kostenkodm/rustdesk-panel/main/scripts/upgrade-online.sh
sh /tmp/rustdesk-panel-online-upgrade.sh
```

Скрипт: скачивает 4 файла, проверяет синтаксис и Compose, останавливает только веб-панель, создаёт резервную копию `state/`, `compose.yaml` и исходников в `/volume2/dsm-recovery`, затем пересоздаёт **только** `rustdesk-panel-web` и запускает `rustdesk-panel-status`. Существующий `rustdesk-panel-exporter` продолжает работу, а HBBS/HBBR не изменяются.

Важно: при ошибке проверки скрипт возвращает прошлые `web.py` и `compose.yaml`, сохраняет существующую `state/` и снова запускает прежнюю веб-панель. База перед обновлением сохранена отдельно.

## Проверка

```sh
cd /volume3/docker/rustdesk-panel
docker compose -f compose.yaml ps
docker logs --tail 15 rustdesk-panel-status
docker logs --tail 15 rustdesk-panel-web
```

Ожидается три контейнера: `rustdesk-panel-exporter`, `rustdesk-panel-web` и `rustdesk-panel-status`.

Откройте `http://192.168.30.150:8765` в доверенной локальной сети/VPN, авторизуйтесь и обновите страницу браузера. Появятся счётчики `Онлайн`, `Офлайн`, `Нет данных`, индикаторы на каждой карточке, фильтр `Доступность` и возраст последнего опроса. Фоновый опрос выполняется автоматически каждые 20 секунд, обновление открытой страницы — вручную.

Устройства, добавленные вручную без регистрации на HBBS, показываются со статусом `Нет данных`.

Если `rustdesk-panel-status` пишет `ok=False`, проверьте, что `NAS_BIND_IP` в `.env` соответствует LAN IP NAS и TCP 21115 доступен из Docker bridge. При необходимости задайте `HBBS_STATUS_HOST=192.168.30.150` в `.env`, затем пересоздайте **только статусный контейнер**:

```sh
docker compose -f compose.yaml up -d --no-deps --force-recreate status
```

Не открывайте веб-порт 8765 в интернет; не публикуйте статус-файл с реальными RustDesk ID. Статус `Онлайн` означает недавнюю регистрацию клиента на HBBS, но не гарантию возможности удалённого управления и не запись о сеансе.
