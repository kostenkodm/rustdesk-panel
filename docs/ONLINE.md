# Онлайн/офлайн устройств RustDesk OSS: проверка протокола

## Почему не используем базу HBBS

В базе `db_v2.sqlite3` хранится регистрация RustDesk ID, но точный онлайн-статус вычисляется в памяти `hbbs` по времени последней регистрации. Нельзя выдавать наличие строки или время создания записи за доступность устройства.

В RustDesk OSS есть запрос `RendezvousMessage.OnlineRequest` в протоколе `rendezvous.proto`. Его обрабатывает TCP-порт `21115` (по умолчанию, `PORT - 1`), где `hbbs` отвечает `OnlineResponse.states`. Каждый бит по порядку входного списка означает, что устройство регистрировалось на сервере в последние 30 секунд. Обработчик TCP допускает этот запрос только в обычном protobuf-пути (не loopback console). Протокол проверен по исходному коду, не по догадке.

Источники: [официальный обработчик HBBS](https://github.com/rustdesk/rustdesk-server/blob/master/src/rendezvous_server.rs), [rendezvous.proto](https://github.com/rustdesk/hbb_common/blob/main/protos/rendezvous.proto), [фрейминг BytesCodec](https://github.com/rustdesk/hbb_common/blob/main/src/bytes_codec.rs).

## Безопасный тест на уже работающем NAS

Тест не перезапускает/не меняет HBBS, HBBR и RustDesk Panel. Из сети отдельного одноразового контейнера выполняется запрос только к опубликованному TCP-порту HBBS на адресе NAS. Вне доверенной LAN/VPN дополнительно пробрасывать порт не нужно.

Команды SSH root:

```sh
cd /volume3/docker/rustdesk-panel
wget -q -O status_probe.py https://raw.githubusercontent.com/kostenkodm/rustdesk-panel/main/status_probe.py
chmod 644 status_probe.py

docker run --rm --network bridge --read-only \
  -v "$PWD/status_probe.py:/app/status_probe.py:ro" \
  -v "$PWD/shared/peers.json:/data/peers.json:ro" \
  python:3.12-alpine python -B /app/status_probe.py \
  --host 192.168.30.150 --port 21115 --peers-json /data/peers.json
```

При успехе будет, например, `HBBS: total=23 online=N offline=23-N`. Число `N` заранее неизвестно. Для диагностики можно добавить `--show-ids`, но не публикуйте в интернете списки реальных RustDesk ID.

При сетевой ошибке, неверном ответе сервера или старом экспорте получим `UNKNOWN`. Это **не равно** `OFFLINE`. Если NAT-путь от Docker до LAN IP не работает, нужно проверить маршрут и опубликованный порт, а не менять базу HBBS.

Важно: запуск на хосте с `--host 127.0.0.1` может попасть в *loopback console* и не вернуть протокольный ответ. Используйте LAN IP NAS из Docker bridge.

## Интеграция в веб-панель 0.3

После проверки запроса на NAS добавлены панельные индикаторы `Онлайн/Офлайн/Нет данных`, время последнего опроса и фильтры. Отдельный контейнер `rustdesk-panel-status` выполняет ограниченные запросы через каждые 20 секунд; при сетевой ошибке статус становится `Нет данных`, а не ложным офлайном. База/ключи HBBS остаются read-only и недоступными для веб-панели. Подробное обновление: [UPGRADE-ONLINE.md](UPGRADE-ONLINE.md).
