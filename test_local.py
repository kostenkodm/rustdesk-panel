"""Smoke tests: python3 test_local.py (no NAS or Docker necessary)."""
import http.client
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import urllib.parse

ROOT = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run():
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
        web.PASSWORD_FILE = root / "password"
        web.PASSWORD_FILE.write_text("example-test-password-12345")
        web.EXPORT.write_text(json.dumps(data))
        web.init_db()

        server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)

        def request(method, path, fields=None, cookie=None):
            headers = {}
            body = None
            if fields is not None:
                body = urllib.parse.urlencode(fields)
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            if cookie:
                headers["Cookie"] = cookie
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            return response, response.read().decode("utf-8")

        try:
            response, body = request("GET", "/")
            assert response.status == 200 and "Пароль администратора" in body

            response, _ = request("POST", "/login", {"password": "example-test-password-12345"})
            assert response.status == 303
            cookie = response.getheader("Set-Cookie").split(";")[0]

            response, body = request("GET", "/", cookie=cookie)
            assert response.status == 200
            assert "123456789" in body and "TEST_PC" in body

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
            print("PASS: exporter, login, inventory, escaping and CSRF checks")
        finally:
            conn.close()
            server.shutdown()


if __name__ == "__main__":
    run()
