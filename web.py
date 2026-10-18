#!/usr/bin/env python3
"""Private inventory web UI for RustDesk OSS. Never writes to HBBS/HBBR."""
from __future__ import annotations

import hashlib
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
input,textarea {{display:block;width:100%;border-radius:8px;border:1px solid var(--edge);background:#101d2b;color:var(--text);padding:10px;font:inherit}}
textarea {{min-height:70px;resize:vertical}} button {{background:var(--blue);border:0;color:#10253c;border-radius:8px;padding:10px 15px;cursor:pointer;font-weight:700}}
a {{color:var(--blue)}} .cards {{display:grid;grid-template-columns:repeat(auto-fill,minmax(285px,1fr));gap:12px}}
.cards article {{margin:0;min-width:0}} .cards strong {{overflow-wrap:anywhere}}
.meta {{color:var(--muted);font-size:13px;overflow-wrap:anywhere}}
.stats {{display:flex;gap:12px;flex-wrap:wrap}} .stats section {{flex:1;min-width:150px}}
.stats b {{display:block;font-size:26px}} .pill {{display:inline-block;border-radius:6px;padding:3px 8px;margin:5px 4px 5px 0;background:#314459;font-size:12px}}
.warning {{padding:13px;background:#433222;border:1px solid #85653e;border-radius:9px;margin:12px 0}}
</style></head><body><main>{content}<p class="muted"><small>RustDesk Panel MVP · Не показывает достоверный онлайн-статус и не управляет правами подключения RustDesk.</small></p></main></body></html>"""


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
            row = db.execute("SELECT csrf,expires FROM sessions WHERE token_hash=?", (key,)).fetchone()
        if row and row["expires"] > int(time.time()):
            return (key, row["csrf"])
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
        return {key: values[0] for key, values in pairs.items() if values}

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
<label>Пароль администратора<input type="password" name="password" required autocomplete="current-password"></label>
<button type="submit">Войти</button></form></section>"""
            self.reply(document("Вход", login))
            return
        if path != "/":
            self.reply(document("Не найдено", "<h1>Страница не найдена</h1>"), 404)
            return

        export, peers = read_peers()
        with connect() as db:
            own = {row["id"]: dict(row) for row in db.execute("SELECT id,alias,group_name,note FROM devices")}
        ids = sorted(set(peers) | set(own), key=str.casefold)
        groups = {row["group_name"] for row in own.values() if row["group_name"]}
        csrf = escape(session[1])
        query = parse_qs(urlsplit(self.path).query).get("q", [""])[0][:120]
        header = f"""<header><div><h1>RustDesk Panel</h1>
<p class="muted">Каталог устройств RustDesk OSS</p></div>
<form action="/logout" method="post"><input type="hidden" name="csrf" value="{csrf}"><button>Выйти</button></form></header>
<div class="stats"><section>Всего устройств<b>{len(ids)}</b></section>
<section>В HBBS<b>{len(peers)}</b></section><section>Групп<b>{len(groups)}</b></section></div>
<section><form action="/" method="get"><label>Поиск по имени, ID, группе и ОС
<input name="q" type="search" value="{escape(query)}"></label><button>Найти</button></form></section>"""
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
            cards.append(f"""<article><strong>{escape(alias)}</strong>
<div class="meta">ID: {escape(device_id)}</div>
<span class="pill">{escape(group or "Без группы")}</span>
<span class="pill">{"Есть в HBBS" if device_id in peers else "Добавлено вручную"}</span>
<p class="meta">{escape(info.get("os","ОС неизвестна"))} · {escape(info.get("username",""))}</p>
<form action="/device" method="post"><input type="hidden" name="csrf" value="{csrf}">
<input type="hidden" name="id" value="{escape(device_id)}">
<label>Название<input name="alias" maxlength="120" value="{escape(saved.get("alias",""))}"></label>
<label>Группа<input name="group_name" maxlength="80" value="{escape(group)}"></label>
<label>Заметка<textarea name="note" maxlength="1200">{escape(saved.get("note",""))}</textarea></label>
<button>Сохранить</button></form></article>""")
        extra = f"""<section><h2>Добавить устройство вручную</h2>
<form action="/device" method="post"><input type="hidden" name="csrf" value="{csrf}">
<label>RustDesk ID<input name="id" maxlength="128" required></label>
<label>Название<input name="alias" maxlength="120"></label>
<label>Группа<input name="group_name" maxlength="80"></label>
<label>Заметка<textarea name="note" maxlength="1200"></textarea></label>
<button>Добавить</button></form></section>"""
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
                row = db.execute("SELECT salt,digest FROM admin WHERE id=1").fetchone()
            digest = hashlib.pbkdf2_hmac("sha256", password.encode(), row["salt"], 600000)
            if not hmac.compare_digest(digest, row["digest"]):
                time.sleep(0.6)
                self.reply(document("Ошибка", '<h1>Неверный пароль</h1><a href="/">Назад</a>'), 403)
                return
            token, csrf = secrets.token_urlsafe(36), secrets.token_urlsafe(32)
            with connect() as db:
                db.execute("INSERT INTO sessions VALUES (?,?,?)",
                           (hashlib.sha256(token.encode()).hexdigest(), csrf, int(time.time()) + SESSION_LIFETIME))
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
        elif path == "/device":
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
