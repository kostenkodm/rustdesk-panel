"""Smoke tests: python3 test_local.py (no NAS or Docker necessary)."""
import http.client
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import threading
import urllib.parse

ROOT = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run():
    # Regression: Docker bind-mounted source file can have a stale inode after mv.
    # Both forward upgrade and rollback must recreate the web container.
    upgrade = (ROOT / "scripts" / "upgrade.sh").read_text("utf-8")
    assert upgrade.count("up -d --no-deps --force-recreate web") == 2
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        old = root / "db_v2.sqlite3"
        with sqlite3.connect(old) as db:
            db.execute("CREATE TABLE peer(id TEXT,created_at TEXT,info TEXT)")
            db.execute("INSERT INTO peer VALUES(?,?,?)", (
                "123456789", "2026-10-18", json.dumps({
                    "device_name": "TEST_PC", "os": "Windows",
                    "username": "someone", "secret": "DO_NOT_EXPORT"
                })
            ))
        exp = load("exporter")
        exp.SOURCE = old
        data = exp.export_once()
        assert data["ok"] and data["devices"][0]["id"] == "123456789"
        assert "DO_NOT_EXPORT" not in json.dumps(data)

        web = load("web")
        web.STATE = root
        web.REGISTRY = root / "panel.sqlite3"
        web.EXPORT = root / "peers.json"
        web.STATUS_FILE = root / "statuses.json"
        web.PASSWORD_FILE = root / "password"
        web.PASSWORD_FILE.write_text("example-test-password-12345")
        web.EXPORT.write_text(json.dumps(data))
        # Emulate an existing 0.1 database to test its in-place migration.
        with sqlite3.connect(web.REGISTRY) as db:
            db.execute("""CREATE TABLE devices (
                id TEXT PRIMARY KEY, alias TEXT NOT NULL DEFAULT '',
                group_name TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT ''
            )""")
            db.execute("INSERT INTO devices(id,alias,group_name,note) VALUES (?,?,?,?)",
                       ("legacy-device", "Old device", "Старые", "legacy note"))
        web.init_db()
        with web.connect() as db:
            row = db.execute("SELECT alias,group_name,note,favorite FROM devices WHERE id=?",
                             ("legacy-device",)).fetchone()
            assert tuple(row) == ("Old device", "Старые", "legacy note", 0)

        server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)

        def request(method, path, fields=None, cookie=None):
            headers = {}
            body = None
            if fields is not None:
                body = urllib.parse.urlencode(fields, doseq=True)
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            if cookie:
                headers["Cookie"] = cookie
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            return response, response.read().decode("utf-8")

        try:
            response, body = request("GET", "/")
            assert response.status == 200 and "Пароль администратора" in body

            response, _ = request("POST", "/login", {"username": "admin", "password": "example-test-password-12345"})
            assert response.status == 303
            cookie = response.getheader("Set-Cookie").split(";")[0]

            response, body = request("GET", "/", cookie=cookie)
            assert response.status == 200
            assert "123456789" in body and "TEST_PC" in body
            assert "Нет данных" in body  # missing status snapshot
            web.STATUS_FILE.write_text(json.dumps({
                "ok": True, "timestamp": int(time.time()),
                "devices": {"123456789": "online", "legacy-device": "offline"}
            }), encoding="utf-8")
            response, body = request("GET", "/", cookie=cookie)
            assert "Онлайн" in body and "Офлайн" in body
            response, body = request("GET", "/?status=online", cookie=cookie)
            assert "TEST_PC" in body and "legacy-device" not in body
            response, body = request("GET", "/?status=offline", cookie=cookie)
            assert "legacy-device" in body and "TEST_PC" not in body
            # Expired / failed status data must never be presented as offline.
            web.STATUS_FILE.write_text(json.dumps({
                "ok": False, "timestamp": int(time.time()),
                "devices": {"123456789": "offline"}
            }))
            response, body = request("GET", "/?status=unknown", cookie=cookie)
            assert "123456789" in body and "Нет данных" in body
            web.STATUS_FILE.write_text(json.dumps({
                "ok": True, "timestamp": int(time.time()) - 1000,
                "devices": {"123456789": "offline"}
            }))
            assert web.read_statuses()[0] == {}
            web.STATUS_FILE.write_text(json.dumps({
                "ok": True, "timestamp": int(time.time()),
                "devices": {"123456789": "online", "legacy-device": "offline"}
            }))

            with web.connect() as db:
                csrf = db.execute("SELECT csrf FROM sessions").fetchone()[0]
            response, _ = request("POST", "/device", {
                "id": "123456789", "alias": "Note<admin>",
                "group_name": "Дом", "csrf": csrf
            }, cookie=cookie)
            assert response.status == 303

            response, body = request("GET", "/", cookie=cookie)
            assert "Note&lt;admin&gt;" in body and "Note<admin>" not in body

            response, _ = request("POST", "/device", {
                "id": "test", "csrf": "wrong"
            }, cookie=cookie)
            assert response.status == 403
            response, _ = request("POST", "/favorite", {
                "id": "123456789", "csrf": csrf
            }, cookie=cookie)
            assert response.status == 303
            with web.connect() as db:
                assert db.execute("SELECT favorite FROM devices WHERE id='123456789'").fetchone()[0] == 1

            response, body = request("GET", "/?favorites=1", cookie=cookie)
            assert response.status == 200 and "123456789" in body
            assert "legacy-device" not in body

            response, _ = request("POST", "/bulk-group", {
                "selected": ["123456789", "legacy-device"],
                "group_name": "Серверы",
                "csrf": csrf
            }, cookie=cookie)
            assert response.status == 303
            with web.connect() as db:
                rows = db.execute(
                    "SELECT id,group_name,note FROM devices WHERE id IN ('123456789','legacy-device')"
                ).fetchall()
            assert len(rows) == 2 and all(r["group_name"] == "Серверы" for r in rows)
            assert next(r for r in rows if r["id"] == "legacy-device")["note"] == "legacy note"

            response, body = request("GET", "/?group=" + urllib.parse.quote("Серверы"),
                                     cookie=cookie)
            assert response.status == 200 and "legacy-device" in body

            response, _ = request("POST", "/device", {
                "id": "123456789", "alias": "=HYPERLINK(1,2)",
                "group_name": "Серверы", "csrf": csrf
            }, cookie=cookie)
            assert response.status == 303
            response, csv_body = request("GET", "/export.csv", cookie=cookie)
            assert response.status == 200
            assert response.getheader("Content-Type").startswith("text/csv")
            assert "'=HYPERLINK(1,2)" in csv_body and "legacy-device" in csv_body
            assert "DO_NOT_EXPORT" not in csv_body

            response, body = request("GET", "/export.csv")
            assert response.getheader("Content-Type").startswith("text/html")
            assert "legacy-device" not in body

            response, _ = request("POST", "/bulk-group", {
                "selected": ["unknown-id"],
                "group_name": "wrong",
                "csrf": csrf
            }, cookie=cookie)
            assert response.status == 400

            print("PASS: migration, login, catalog, group filter, bulk edit, favorite, CSV and CSRF")
        finally:
            conn.close()
            server.shutdown()


if __name__ == "__main__":
    run()
