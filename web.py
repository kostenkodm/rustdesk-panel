#!/usr/bin/env python3
"""Private inventory web UI for RustDesk OSS. Never writes to HBBS/HBBR."""
from __future__ import annotations

import csv
import accounts
import panel_pages
import hashlib
import io
import hmac
import html
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

STATE = Path(os.getenv("STATE_DIR", "/state"))
REGISTRY = STATE / "manager.sqlite3"
EXPORT = Path(os.getenv("PEERS_EXPORT", "/peers/peers.json"))
PASSWORD_FILE = Path(os.getenv("ADMIN_PASSWORD_FILE", "/run/secrets/admin_password"))
STATUS_FILE = Path(os.getenv("STATUS_FILE", "/peers/statuses.json"))
STATUS_MAX_AGE = 75
PORT = int(os.getenv("PORT", "8765"))
SESSION_LIFETIME = 8 * 3600
MAX_BODY = 20000


def escape(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def connect():
    db = sqlite3.connect(REGISTRY, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    return db


def init_db():
    STATE.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS admin (
                id INTEGER PRIMARY KEY CHECK(id=1),
                salt BLOB NOT NULL, digest BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                csrf TEXT NOT NULL,
                expires INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY,
                alias TEXT NOT NULL DEFAULT '',
                group_name TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT ''
            );
        """)
        # Upgrade existing 0.1 installations without losing device notes.
        columns = {row[1] for row in db.execute("PRAGMA table_info(devices)")}
        if "favorite" not in columns:
            db.execute("ALTER TABLE devices ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0")
        if not db.execute("SELECT 1 FROM admin WHERE id=1").fetchone():
            if not PASSWORD_FILE.is_file():
                raise RuntimeError("admin-password отсутствует")
            password = PASSWORD_FILE.read_text("utf-8").rstrip("\r\n")
            if len(password) < 14:
                raise RuntimeError("Пароль администратора должен иметь не менее 14 символов")
            salt = secrets.token_bytes(24)
            digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600000)
            db.execute("INSERT INTO admin VALUES (1, ?, ?)", (salt, digest))
        db.execute("DELETE FROM sessions WHERE expires <= ?", (int(time.time()),))
        accounts.schema(db)


def read_peers():
    try:
        export = json.loads(EXPORT.read_text("utf-8"))
        if not isinstance(export, dict):
            raise ValueError("bad data")
        devices = export.get("devices", [])
        if not isinstance(devices, list):
            devices = []
        return export, {
            item["id"]: item for item in devices
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
    except (OSError, ValueError, TypeError):
        return {"ok": False, "error": "Экспорт HBBS ещё не доступен"}, {}


def read_statuses(now=None):
    """Use only fresh successful HBBS snapshots; otherwise all statuses unknown."""
    stamp = int(time.time() if now is None else now)
    try:
        data = json.loads(STATUS_FILE.read_text("utf-8"))
        t = data.get("timestamp")
        if (data.get("ok") is not True
                or not isinstance(t, int)
                or not 0 <= stamp - t <= STATUS_MAX_AGE):
            return {}, None
        raw = data.get("devices")
        if not isinstance(raw, dict):
            return {}, None
        states = {key: state for key, state in raw.items()
                  if isinstance(key, str) and state in ("online", "offline")}
        return states, stamp - t
    except (OSError, ValueError, TypeError, AttributeError):
        return {}, None


def safe_csv_cell(value):
    text = str(value if value is not None else "")
    # Prevent formula execution on opening an export in Excel or LibreOffice.
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def export_inventory_csv():
    _state, peers = read_peers()
    with connect() as db:
        own = {row["id"]: dict(row) for row in db.execute(
            "SELECT id,alias,group_name,note,favorite FROM devices"
        )}
    result = io.StringIO(newline="")
    writer = csv.writer(result)
    writer.writerow(["RustDesk ID", "Name", "Group", "Favorite", "OS", "Source", "Note"])
    for device_id in sorted(set(peers) | set(own), key=str.casefold):
        saved = own.get(device_id, {})
        info = peers.get(device_id, {})
        values = [
            device_id,
            saved.get("alias") or info.get("device_name") or device_id,
            saved.get("group_name") or "",
            "yes" if saved.get("favorite") else "no",
            info.get("os") or "",
            "HBBS" if device_id in peers else "manual",
            saved.get("note") or "",
        ]
        writer.writerow([safe_csv_cell(value) for value in values])
    return "\ufeff" + result.getvalue()


def document(title, content):
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title>
<style>
:root {{color-scheme:dark;--bg:#101722;--panel:#192739;--edge:#34485d;--text:#edf4fc;--muted:#aabed4;--blue:#91c9ff}}
* {{box-sizing:border-box}} body {{background:var(--bg);color:var(--text);font:15px/1.5 system-ui,sans-serif;margin:0}}
main {{max-width:1200px;margin:auto;padding:30px 20px}}
h1 {{font-size:27px;margin:0}} h2 {{font-size:18px;margin:0 0 12px}}
header {{display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap;margin-bottom:22px}}
section,article {{border:1px solid var(--edge);background:var(--panel);padding:18px;border-radius:13px;margin-bottom:14px}}
small,.muted {{color:var(--muted)}} label {{display:block;color:var(--muted);margin:10px 0;font-size:13px}}
input,textarea,select {{display:block;width:100%;border-radius:8px;border:1px solid var(--edge);background:#101d2b;color:var(--text);padding:10px;font:inherit}}
textarea {{min-height:70px;resize:vertical}} button {{background:var(--blue);border:0;color:#10253c;border-radius:8px;padding:10px 15px;cursor:pointer;font-weight:700}}
a {{color:var(--blue)}} .cards {{display:grid;grid-template-columns:repeat(auto-fill,minmax(285px,1fr));gap:12px}}
.cards article {{margin:0;min-width:0}} .cards strong {{overflow-wrap:anywhere}}
.meta {{color:var(--muted);font-size:13px;overflow-wrap:anywhere}}
.stats {{display:flex;gap:12px;flex-wrap:wrap}} .stats section {{flex:1;min-width:150px}}
.stats b {{display:block;font-size:26px}} .pill {{display:inline-block;border-radius:6px;padding:3px 8px;margin:5px 4px 5px 0;background:#314459;font-size:12px}}
.warning {{padding:13px;background:#433222;border:1px solid #85653e;border-radius:9px;margin:12px 0}}
.controls {{display:flex;align-items:end;flex-wrap:wrap;gap:14px}}
.controls label {{flex:1;min-width:170px}}
.controls select {{display:block;width:100%;border-radius:8px;border:1px solid var(--edge);background:#101d2b;color:var(--text);padding:10px;font:inherit}}
.controls input[type="checkbox"],.device-pick {{width:auto;display:inline-block;margin-right:7px}}
.device-head {{display:flex;gap:10px;align-items:start;justify-content:space-between}}
.device-actions {{display:flex;align-items:center;gap:10px}}
.device-actions form {{display:inline}}
.favorite-button {{background:#33485c;color:#eaf2ff}}
.favorite-button[aria-pressed="true"] {{background:#f7dc91;color:#372600}}
.action-link {{margin-left:10px}}
.status-online {{background:#214c38;color:#b8efd1}}
.status-offline {{background:#414653;color:#e6e9ef}}
.status-unknown {{background:#614e2e;color:#ffe1a3}}


</style></head><body><main>{content}<p class="muted"><small>RustDesk Panel 0.4 · Онлайн-статусы из HBBS являются моментальным снимком; панель не управляет правами подключений RustDesk.</small></p></main></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Do not log request paths (which may contain device IDs).
        print(f"request status: {args[-1] if args else 'unknown'}", flush=True)

    def reply(self, content, status=200):
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, target="/", cookie=None):
        self.send_response(303)
        self.send_header("Location", target)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def current_session(self):
        try:
            jar = cookies.SimpleCookie()
            jar.load(self.headers.get("Cookie", ""))
            token = jar["rdm_session"].value
        except (KeyError, cookies.CookieError):
            return None
        if len(token) > 200:
            return None
        key = hashlib.sha256(token.encode()).hexdigest()
        with connect() as db:
            row = db.execute("SELECT csrf,expires,user_id FROM sessions WHERE token_hash=?", (key,)).fetchone()
            if row and row["expires"] > int(time.time()) and row["user_id"]:
                user = accounts.user_by_id(db, row["user_id"])
                if user:
                    return (key, row["csrf"], user)
        return None

    def parse_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if not (0 < length <= MAX_BODY):
            return None
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
            return None
        pairs = parse_qs(self.rfile.read(length).decode("utf-8", "replace"), keep_blank_values=True)
        return {key: values if key == "selected" else values[0]
                for key, values in pairs.items() if values}

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/healthz":
            body = b"OK"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        session = self.current_session()
        if not session:
            login = """<h1>RustDesk Panel</h1><p class="muted">Локальная панель управления устройствами</p>
<section style="max-width:420px"><form action="/login" method="post">
<label>Логин<input name="username" required maxlength="64" autocomplete="username" value="admin"></label>
<label>Пароль<input type="password" name="password" required autocomplete="current-password"></label>
<button type="submit">Войти</button></form></section>"""
            self.reply(document("Вход", login))
            return
        user = session[2]
        if path == "/users":
            if user["role"] != "admin":
                self.send_error(403)
                return
            with connect() as db:
                body = panel_pages.users_page(db, user, session[1])
            self.reply(document("Учётные записи", body))
            return
        if path == "/books":
            params = parse_qs(urlsplit(self.path).query)
            book_id = params.get("book", [""])[0]
            _export, peers = read_peers()
            try:
                with connect() as db:
                    body = panel_pages.books_page(db, user, session[1], book_id, peers)
                self.reply(document("Адресные книги", body))
            except (accounts.BadInput, accounts.AccessDenied):
                self.send_error(403)
            return
        if path in ("/book/export.csv", "/book/export.json"):
            params = parse_qs(urlsplit(self.path).query)
            try:
                book_id = int(params.get("book", [""])[0])
                with connect() as db:
                    book = accounts.book_access(db, user, book_id)
                    contacts = accounts.contacts_in_book(db, book_id)
                if path.endswith(".csv"):
                    result = io.StringIO(newline="")
                    writer = csv.writer(result)
                    writer.writerow(["RustDesk ID", "Name", "Tags", "Note"])
                    for entry in contacts:
                        writer.writerow([safe_csv_cell(entry[field]) for field in
                                         ("peer_id", "alias", "tags", "note")])
                    output = ("\ufeff" + result.getvalue()).encode("utf-8")
                    content_type, extension = "text/csv", "csv"
                else:
                    output = json.dumps({"name": book["name"], "entries": contacts},
                                        ensure_ascii=False).encode("utf-8")
                    content_type, extension = "application/json", "json"
                self.send_response(200)
                self.send_header("Content-Type", content_type + "; charset=utf-8")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="rustdesk-address-book.{extension}"')
                self.send_header("Content-Length", str(len(output)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(output)
            except (ValueError, accounts.AccessDenied):
                self.send_error(403)
            return
        if path == "/export.csv":
            body = export_inventory_csv().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="rustdesk-devices.csv"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
            return
        if path != "/":
            self.reply(document("Не найдено", "<h1>Страница не найдена</h1>"), 404)
            return

        export, peers = read_peers()
        states, status_age = read_statuses()
        with connect() as db:
            own = {row["id"]: dict(row) for row in db.execute(
                "SELECT id,alias,group_name,note,favorite FROM devices"
            )}
        ids = sorted(set(peers) | set(own),
                     key=lambda item: (
                         not bool(own.get(item, {}).get("favorite")),
                         (own.get(item, {}).get("alias") or peers.get(item, {}).get("device_name") or item).casefold()
                     ))
        groups = sorted({row["group_name"] for row in own.values() if row["group_name"]},
                        key=str.casefold)
        csrf = escape(session[1])
        params = parse_qs(urlsplit(self.path).query)
        query = params.get("q", [""])[0][:120]
        group_filter = params.get("group", [""])[0][:80]
        favorites_only = params.get("favorites", [""])[0] == "1"
        status_filter = params.get("status", [""])[0]
        if status_filter not in ("online", "offline", "unknown"):
            status_filter = ""
        def device_status(device_id):
            return states.get(device_id, "unknown")
        counts = {name: sum(device_status(device_id) == name for device_id in ids)
                  for name in ("online", "offline", "unknown")}
        status_options = []
        for value, label in (("", "Любой статус"), ("online", "Онлайн"),
                             ("offline", "Офлайн"), ("unknown", "Нет данных")):
            status_options.append('<option value="' + value + '"' +
                                  (' selected' if status_filter == value else '') +
                                  '>' + label + '</option>')
        status_info = (f"Последняя проверка HBBS: {status_age} сек. назад"
                       if status_age is not None else
                       "Статус HBBS недоступен или устарел")
        options = ['<option value="">Все группы</option>']
        options.append('<option value="__ungrouped__"' +
                       (' selected' if group_filter == "__ungrouped__" else '') +
                       '>Без группы</option>')
        for value in groups:
            options.append('<option value="' + escape(value) + '"' +
                           (' selected' if group_filter == value else '') +
                           '>' + escape(value) + '</option>')
        bulk_html = "" if user["role"] == "viewer" else f"""<section><h2>Массовое изменение группы</h2>
<form action="/bulk-group" method="post" id="bulk-form" class="controls">
<input type="hidden" name="csrf" value="{csrf}">
<label>Новая группа для отмеченных устройств
<input name="group_name" maxlength="80" placeholder="Например, Дом или Серверы"></label>
<button>Применить к выбранным</button></form>
<small>Отметь устройства ниже. Пустая группа убирает привязку.</small></section>"""
        header = f"""<header><div><h1>RustDesk Panel <small>0.4</small></h1>
<p class="muted">Каталог устройств RustDesk OSS</p></div>
<nav><a href="/books">Адресные книги</a> &nbsp;
{'<a href="/users">Пользователи</a> &nbsp;' if user["role"] == "admin" else ''}
<span class="muted">{escape(user["username"])} · {escape(user["role"])}</span></nav>
<form action="/logout" method="post"><input type="hidden" name="csrf" value="{csrf}"><button>Выйти</button></form></header>
<div class="stats"><section>Всего устройств<b>{len(ids)}</b></section>
<section>В реестре HBBS<b>{len(peers)}</b></section><section>Групп<b>{len(groups)}</b></section>
<section>Избранных<b>{sum(bool(v.get("favorite")) for v in own.values())}</b></section>
<section>Онлайн<b>{counts["online"]}</b></section>
<section>Офлайн<b>{counts["offline"]}</b></section>
<section>Нет данных<b>{counts["unknown"]}</b></section></div>
<p class="muted">{escape(status_info)}. Для новых сведений обновите страницу.</p>
<section><form action="/" method="get" class="controls">
<label>Поиск<input name="q" type="search" value="{escape(query)}" placeholder="ID, название, ОС, группа"></label>
<label>Группа<select name="group">{''.join(options)}</select></label>
<label>Доступность<select name="status">{''.join(status_options)}</select></label>
<label><input type="checkbox" name="favorites" value="1" {'checked' if favorites_only else ''}>Только избранные</label>
<button>Фильтровать</button><a class="action-link" href="/export.csv">Скачать CSV</a>
</form></section>
{bulk_html}"""
        if not export.get("ok"):
            header += f'<div class="warning">Чтение HBBS: {escape(export.get("error","нет данных"))}. Ручные записи доступны.</div>'
        cards = []
        for device_id in ids:
            info = peers.get(device_id, {})
            saved = own.get(device_id, {})
            alias = saved.get("alias") or info.get("device_name") or device_id
            group = saved.get("group_name") or ""
            haystack = " ".join([device_id, alias, group, str(info.get("os","")), str(info.get("username",""))]).casefold()
            if query.casefold() not in haystack:
                continue
            if group_filter == "__ungrouped__" and group:
                continue
            if group_filter and group_filter != "__ungrouped__" and group_filter != group:
                continue
            favorite = bool(saved.get("favorite", 0))
            if favorites_only and not favorite:
                continue
            status = device_status(device_id)
            if status_filter and status != status_filter:
                continue
            status_label = {"online": "Онлайн", "offline": "Офлайн",
                            "unknown": "Нет данных"}[status]
            selection_html = (f'<input form="bulk-form" class="device-pick" type="checkbox" '
                              f'name="selected" value="{escape(device_id)}">'
                              if user["role"] != "viewer" else "")
            favorite_html = (f"""<form action="/favorite" method="post">
<input type="hidden" name="csrf" value="{csrf}">
<input type="hidden" name="id" value="{escape(device_id)}">
<button class="favorite-button" type="submit" aria-label="Избранное" aria-pressed="{'true' if favorite else 'false'}">{'★' if favorite else '☆'}</button>
</form>""" if user["role"] != "viewer" else "")
            editor_html = (f"""<form action="/device" method="post">
<input type="hidden" name="csrf" value="{csrf}">
<input type="hidden" name="id" value="{escape(device_id)}">
<label>Название<input name="alias" maxlength="120" value="{escape(saved.get("alias",""))}"></label>
<label>Группа<input name="group_name" maxlength="80" value="{escape(group)}"></label>
<label>Заметка<textarea name="note" maxlength="1200">{escape(saved.get("note",""))}</textarea></label>
<button>Сохранить</button></form>""" if user["role"] != "viewer" else
            f'<p class="meta">{escape(saved.get("note",""))}</p>')
            cards.append(f"""<article>
<div class="device-head"><div>
{selection_html}<strong>{escape(alias)}</strong>
<div class="meta">ID: {escape(device_id)}</div></div>
{favorite_html}</div>
<span class="pill">{escape(group or "Без группы")}</span>
<span class="pill status-{status}">{status_label}</span>
<span class="pill">{"В реестре HBBS" if device_id in peers else "Добавлено вручную"}</span>
<p class="meta">{escape(info.get("os","ОС неизвестна"))} · {escape(info.get("username",""))}</p>
{editor_html}</article>""")
        extra = f"""<section><h2>Добавить устройство вручную</h2>
<form action="/device" method="post"><input type="hidden" name="csrf" value="{csrf}">
<label>RustDesk ID<input name="id" maxlength="128" required></label>
<label>Название<input name="alias" maxlength="120"></label>
<label>Группа<input name="group_name" maxlength="80"></label>
<label>Заметка<textarea name="note" maxlength="1200"></textarea></label>
<button>Добавить</button></form></section>""" if user["role"] != "viewer" else ""
        header += f'<p class="muted">Показано устройств: {len(cards)} из {len(ids)}. Статусы получены из OnlineRequest HBBS; это не проверка доступности удалённого рабочего стола.</p>'
        self.reply(document("RustDesk Panel", header + '<div class="cards">' + "".join(cards) + "</div>" + extra))

    def do_POST(self):
        path = urlsplit(self.path).path
        data = self.parse_body()
        if data is None:
            self.send_error(400)
            return
        if path == "/login":
            password = data.get("password", "")
            if not (1 <= len(password) <= 500):
                self.send_error(403)
                return
            with connect() as db:
                user = accounts.authenticate(db, data.get("username", ""), password)
            if not user:
                time.sleep(0.6)
                self.reply(document("Ошибка", '<h1>Неверный пароль</h1><a href="/">Назад</a>'), 403)
                return
            token, csrf = secrets.token_urlsafe(36), secrets.token_urlsafe(32)
            with connect() as db:
                db.execute("INSERT INTO sessions(token_hash,csrf,expires,user_id) VALUES (?,?,?,?)",
                           (hashlib.sha256(token.encode()).hexdigest(), csrf,
                            int(time.time()) + SESSION_LIFETIME, user["id"]))
            self.redirect(cookie=f"rdm_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_LIFETIME}")
            return

        session = self.current_session()
        if not session or not hmac.compare_digest(data.get("csrf", ""), session[1]):
            self.send_error(403)
            return
        if path == "/logout":
            with connect() as db:
                db.execute("DELETE FROM sessions WHERE token_hash=?", (session[0],))
            self.redirect(cookie="rdm_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
        elif path in ("/user/create", "/user/update", "/book/create",
                       "/book/contact", "/book/remove", "/book/import-hbbs"):
            user = session[2]
            try:
                if path in ("/user/create", "/user/update") and user["role"] != "admin":
                    raise accounts.AccessDenied("Только администратор")
                if path in ("/book/create", "/book/contact", "/book/remove",
                             "/book/import-hbbs") and user["role"] == "viewer":
                    raise accounts.AccessDenied("Нет прав на изменение")
                with connect() as db:
                    if path == "/user/create":
                        accounts.new_user(db, data.get("username", ""),
                                          data.get("password", ""), data.get("role", "viewer"))
                    elif path == "/user/update":
                        accounts.edit_user(db, user["id"], int(data.get("id", "")),
                                           data.get("role", ""), int(data.get("active", "0")),
                                           data.get("password", ""))
                    elif path == "/book/create":
                        accounts.create_book(db, user, data.get("name", ""),
                                             data.get("scope", "personal") == "personal")
                    else:
                        book_id = int(data.get("book", ""))
                        accounts.book_access(db, user, book_id, edit=True)
                        if path == "/book/contact":
                            accounts.save_contact(
                                db, book_id, data.get("peer_id", ""), data.get("alias", ""),
                                data.get("tags", ""), data.get("note", ""))
                        elif path == "/book/remove":
                            accounts.delete_contact(db, book_id, data.get("peer_id", ""))
                        else:
                            export, peers = read_peers()
                            if export.get("ok") is not True or export.get("stale"):
                                raise accounts.BadInput("Нет актуального экспорта HBBS")
                            accounts.import_hbbs(db, book_id, peers)
                if path.startswith("/user"):
                    self.redirect("/users")
                else:
                    self.redirect("/books" if path == "/book/create"
                                  else f"/books?book={book_id}")
            except accounts.AccessDenied:
                self.send_error(403)
            except (accounts.BadInput, ValueError) as exc:
                self.reply(document("Ошибка", f'<h1>Некорректные данные</h1><p>{escape(exc)}</p>'
                                    '<a href="/">Вернуться</a>'), 400)
            return
        elif path == "/favorite":
            if session[2]["role"] == "viewer":
                self.send_error(403)
                return
            device_id = data.get("id", "").strip()
            if not device_id or len(device_id) > 128:
                self.send_error(400)
                return
            with connect() as db:
                row = db.execute("SELECT favorite FROM devices WHERE id=?", (device_id,)).fetchone()
                if row:
                    db.execute("UPDATE devices SET favorite=? WHERE id=?",
                               (0 if row["favorite"] else 1, device_id))
                else:
                    _, peers = read_peers()
                    if device_id not in peers:
                        self.send_error(404)
                        return
                    db.execute("INSERT INTO devices(id,favorite) VALUES (?,1)", (device_id,))
            self.redirect()
        elif path == "/bulk-group":
            if session[2]["role"] == "viewer":
                self.send_error(403)
                return
            selected = data.get("selected", [])
            if not isinstance(selected, list) or not (1 <= len(selected) <= 500):
                self.send_error(400)
                return
            group_name = data.get("group_name", "").strip()
            if len(group_name) > 80:
                self.send_error(400)
                return
            _, peers = read_peers()
            with connect() as db:
                known = set(peers) | {row[0] for row in db.execute("SELECT id FROM devices")}
                ids = list(dict.fromkeys(selected))
                if any(not item or len(item) > 128 or item not in known for item in ids):
                    self.send_error(400)
                    return
                db.executemany(
                    """INSERT INTO devices(id,group_name) VALUES (?,?)
                    ON CONFLICT(id) DO UPDATE SET group_name=excluded.group_name""",
                    [(device_id, group_name) for device_id in ids])
            self.redirect()
        elif path == "/device":
            if session[2]["role"] == "viewer":
                self.send_error(403)
                return
            device_id = data.get("id", "").strip()
            if not device_id or len(device_id) > 128 or any(ord(c) < 32 for c in device_id):
                self.send_error(400)
                return
            with connect() as db:
                db.execute("""INSERT INTO devices(id,alias,group_name,note) VALUES (?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET alias=excluded.alias,
                    group_name=excluded.group_name,note=excluded.note""",
                    (device_id, data.get("alias","")[:120], data.get("group_name","")[:80], data.get("note","")[:1200]))
            self.redirect()
        else:
            self.send_error(404)


def main():
    init_db()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"RustDesk Panel ready on port {PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
