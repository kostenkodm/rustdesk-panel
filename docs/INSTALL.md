# Установка RustDesk Panel на Synology NAS (DSM / Docker)

Панель устанавливается **рядом с существующими HBBS/HBBR** и не изменяет их конфигурацию. Команды для NAS выполняются по SSH с правами root.

## Проверенные сведения об исходной инфраструктуре

- NAS: 192.168.30.150, пользователь SSH kostenkodm, порт SSH 99.
- HBBS Docker mount: /volume3/docker/rustdeskhbbs -> /root.
- HBBR Docker mount: /volume3/docker/rustdeskhbbr -> /root.
- HBBS/HBBR работают в отдельном Compose-стеке и не будут остановлены установкой панели.
- Требуется Docker с поддержкой docker compose.

Для другого NAS поменяйте IP, пути и UID/GID в .env.

## 1. Проверка HBBS и Docker

На NAS:

\`\`\`sh
sudo -i
docker ps --format 'table {{.Names}}\t{{.Status}}'
docker compose version
ls -lh /volume3/docker/rustdeskhbbs/db_v2.sqlite3
\`\`\`

Если базы нет, **не создавайте пустой файл**. Выясните реальный путь БД HBBS; приложение сможет работать с ручными карточками, но импорт устройств без реальной базы будет недоступен.

## 2. Скачивание проекта

Репозиторий **приватный**, поэтому скачать его нужно после входа в свой GitHub.

**На Windows:** откройте https://github.com/kostenkodm/rustdesk-panel, нажмите Code → Download ZIP. Архив обычно называется rustdesk-panel-main.zip.

В PowerShell, из папки с архивом:

\`\`\`powershell
scp -O -P 99 .\rustdesk-panel-main.zip kostenkodm@192.168.30.150:/tmp/
\`\`\`

На NAS под root (не перезаписывайте уже существующую установку):

\`\`\`sh
test ! -e /volume3/docker/rustdesk-panel || {
  echo 'STOP: /volume3/docker/rustdesk-panel уже существует'
  exit 1
}
unzip -q /tmp/rustdesk-panel-main.zip -d /volume3/docker/
mv /volume3/docker/rustdesk-panel-main /volume3/docker/rustdesk-panel
cd /volume3/docker/rustdesk-panel
\`\`\`

Если на NAS установлен Git и настроен доступ к приватному GitHub, можно вместо ZIP выполнить \`git clone git@github.com:kostenkodm/rustdesk-panel.git /volume3/docker/rustdesk-panel\`.

## 3. Настройка переменных

\`\`\`sh
cd /volume3/docker/rustdesk-panel
cp .env.example .env
cat .env
\`\`\`

По умолчанию в шаблоне:

\`\`\`dotenv
NAS_BIND_IP=192.168.30.150
HBBS_DIR=/volume3/docker/rustdeskhbbs
PANEL_UID=1026
PANEL_GID=100
\`\`\`

Не указывайте 0.0.0.0 в NAS_BIND_IP: панель рассчитана на локальную сеть и VPN.

## 4. Подготовка каталогов

\`\`\`sh
cd /volume3/docker/rustdesk-panel
mkdir -p state shared
chown 1026:100 state shared
chmod 700 state
chmod 770 shared
\`\`\`

Если меняли PANEL_UID/PANEL_GID в .env, поменяйте их и в команде chown.

## 5. Пароль администратора

Под root в интерактивной SSH-сессии:

\`\`\`sh
cd /volume3/docker/rustdesk-panel
umask 077
printf 'Пароль администратора (минимум 14 символов): '
stty -echo
IFS= read -r PW
stty echo
printf '\n'
printf '%s\n' "$PW" > admin-password
unset PW
chown 1026:100 admin-password
chmod 600 admin-password
\`\`\`

Пароль помещается в базу панели в виде PBKDF2-SHA256 хеша **только при первой инициализации**. Последующая правка файла \`admin-password\` не сменит пароль существующего администратора.

Не добавляйте admin-password и .env в Git.

## 6. Проверка и запуск

\`\`\`sh
cd /volume3/docker/rustdesk-panel
docker compose -f compose.yaml config -q
docker compose -f compose.yaml up -d
docker compose -f compose.yaml ps
docker logs --tail 30 rustdesk-panel-exporter
docker logs --tail 30 rustdesk-panel-web
\`\`\`

Откройте из локальной сети или через VPN:

**http://192.168.30.150:8765**

Порт 8765 не должен пробрасываться через OpenWrt в интернет. Для доступа из недоверенной сети нужен TLS, защита входа и дополнительная проверка безопасности.

## 7. Диагностика

Если список пуст: \`docker logs --tail 50 rustdesk-panel-exporter\` и \`ls -l /volume3/docker/rustdeskhbbs/db_v2.sqlite3\`. Проверить, что в базе действительно есть таблица \`peer\`: схема БД зависит от версии HBBS.

Если на экране ошибка прав: \`ls -ldn state shared admin-password\`; веб-контейнер работает под UID/GID из .env.

Если порт уже занят: \`docker ps --format 'table {{.Names}}\t{{.Ports}}'\` и выберите другой порт **снаружи** контейнера в compose.yaml.

Экспортёр работает без сети и только читает существующий каталог HBBS; он не изменяет базу, ключи и работающие контейнеры.

## 8. Остановка и удаление только панели

\`\`\`sh
cd /volume3/docker/rustdesk-panel
docker compose -f compose.yaml down
\`\`\`

Не используйте глобальные команды очистки Docker и не меняйте существующие HBBS/HBBR. Данные панели остаются в state/, экспорт — в shared/.

## 9. Проверка тестов вне NAS

На Windows, Linux или macOS с Python 3.12+:

\`\`\`sh
python3 test_local.py
\`\`\`

Тест создаёт временную искусственную БД HBBS, проверяет импорт, вход в панель, экранирование HTML и проверку CSRF, затем удаляет тестовые данные.

## Ограничения MVP

- Нет достоверных онлайн-статусов, аудита реальных RustDesk-сеансов, централизованного ограничения RustDesk-подключений и синхронизации адресной книги внутри клиентов.
- Роли текущей панели ограничены одним администратором.
- Веб-интерфейс использует HTTP: только доверенная локальная сеть или VPN.
