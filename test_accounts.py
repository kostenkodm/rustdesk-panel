"""Smoke and permission tests for multiuser directory, no NAS or HBBS needed."""
import http.client
import json
import sqlite3
import tempfile
import threading
import urllib.parse
from pathlib import Path

import accounts
import web


def main():
    # Guard against the exact Synology failure: docker exec python -c starts
    # outside /app, so explicit module path insertion is required.
    upgrade = (Path(__file__).parent / "scripts" / "upgrade-accounts.sh").read_text("utf-8")
    assert "sys.path.insert(0, \"/app\"); import accounts, panel_pages" in upgrade
    assert "HTTP /healthz: " in upgrade and "Схема SQLite" in upgrade
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        web.STATE = root
        web.REGISTRY = root / "manager.sqlite3"
        web.EXPORT = root / "peers.json"
        web.STATUS_FILE = root / "statuses.json"
        assert web.connect_uri("123456789") == "rustdesk://connection/new/123456789"
        assert "%22" in web.connect_uri('123"456')
        assert "%3C" in web.connect_uri("123<script>")
        try:
            web.connect_uri("id\nmalicious")
            assert False, "Unsafe ID accepted"
        except ValueError:
            pass
        web.PASSWORD_FILE = root / "admin-password"
        web.PASSWORD_FILE.write_text("initial-admin-password-12345")
        web.EXPORT.write_text(json.dumps({
            "ok": True, "devices": [{"id": "123456789", "device_name": "HOME-PC"}]
        }))
        # Existing 0.3 installation: saved device aliases must survive migration.
        with sqlite3.connect(web.REGISTRY) as db:
            db.execute("""CREATE TABLE devices(
                id TEXT PRIMARY KEY, alias TEXT NOT NULL DEFAULT '',
                group_name TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '')""")
            db.execute("INSERT INTO devices VALUES (?,?,?,?)",
                       ("legacy-device", "Old PC", "Office", "keep this note"))
        web.init_db()
        with web.connect() as db:
            admin = accounts.authenticate(db, "admin", "initial-admin-password-12345")
            assert admin and admin["role"] == "admin"
            books = accounts.books_visible(db, admin)
            assert books
            contacts = accounts.contacts_in_book(db, books[0]["id"])
            assert any(c["note"] == "keep this note" for c in contacts)
            accounts.new_user(db, "editor", "editor-password-123456", "editor")
            accounts.new_user(db, "viewer", "viewer-password-123456", "viewer")
            accounts.new_user(db, "other", "other-password-1234567", "editor")
            editor = accounts.authenticate(db, "editor", "editor-password-123456")
            viewer = accounts.authenticate(db, "viewer", "viewer-password-123456")
            other = accounts.authenticate(db, "other", "other-password-1234567")
            assert editor and viewer and other
            accounts.create_book(db, editor, "Только для editor", personal=True)
            personal_id = next(b["id"] for b in accounts.books_visible(db, editor)
                               if b["owner_user_id"] is not None)
            try:
                accounts.book_access(db, other, personal_id)
                assert False, "private book leaked to another account"
            except accounts.AccessDenied:
                pass
            try:
                accounts.create_book(db, editor, "Forbidden shared book")
                assert False, "editor created global address book"
            except accounts.AccessDenied:
                pass
            try:
                accounts.book_access(db, viewer, books[0]["id"], edit=True)
                assert False, "viewer can edit shared book"
            except accounts.AccessDenied:
                pass
            try:
                accounts.edit_user(db, admin["id"], admin["id"], "viewer", 1)
                assert False, "last admin downgrade allowed"
            except accounts.BadInput:
                pass
            accounts.save_contact(db, personal_id, "999000", "Private Server", "NAS, home", "private")
            assert len(accounts.contacts_in_book(db, personal_id)) == 1
            accounts.delete_contact(db, personal_id, "999000")
            assert not accounts.contacts_in_book(db, personal_id)
            accounts.import_hbbs(db, books[0]["id"], {"123456789": {"device_name": "HOME-PC"}})
            assert any(row["peer_id"] == "123456789"
                       for row in accounts.contacts_in_book(db, books[0]["id"]))

        server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        def request(method, path, form=None, cookie=None):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            headers = {}
            data = None
            if form is not None:
                data = urllib.parse.urlencode(form, doseq=True)
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            if cookie:
                headers["Cookie"] = cookie
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            result = (resp.status, resp.read().decode("utf-8"), resp.getheader("Set-Cookie"))
            conn.close()
            return result

        def login(username, password):
            status, _, cookies = request("POST", "/login",
                                         {"username": username, "password": password})
            assert status == 303 and cookies
            return cookies.split(";", 1)[0]

        try:
            admin_cookie = login("admin", "initial-admin-password-12345")
            editor_cookie = login("editor", "editor-password-123456")
            viewer_cookie = login("viewer", "viewer-password-123456")
            assert request("GET", "/users", cookie=admin_cookie)[0] == 200
            assert request("GET", "/users", cookie=viewer_cookie)[0] == 403
            assert "Адресные книги" in request("GET", "/", cookie=viewer_cookie)[1]
            # Clicking the card opens local RustDesk client, never exposes passwords.
            assert 'href="rustdesk://connection/new/123456789"' in request(
                "GET", "/", cookie=viewer_cookie)[1]
            assert request("GET", "/books", cookie=editor_cookie)[0] == 200
            assert request("GET", "/book/export.json?book=99999", cookie=viewer_cookie)[0] == 403
            with web.connect() as db:
                csrf_viewer = db.execute(
                    "SELECT csrf FROM sessions WHERE user_id=?",
                    (viewer["id"],),
                ).fetchone()[0]
                csrf_editor = db.execute(
                    "SELECT csrf FROM sessions WHERE user_id=?",
                    (editor["id"],),
                ).fetchone()[0]
                shared_id = accounts.books_visible(db, admin)[0]["id"]
            assert request("POST", "/device", {"id": "n", "csrf": csrf_viewer},
                           viewer_cookie)[0] == 403
            assert request("POST", "/book/contact", {
                "book": str(shared_id), "peer_id": "x", "csrf": csrf_viewer
            }, viewer_cookie)[0] == 403
            assert request("POST", "/user/create", {
                "username": "ev", "password": "test-password-1234567",
                "role": "admin", "csrf": csrf_editor
            }, editor_cookie)[0] == 403
            assert request("POST", "/book/contact", {
                "book": str(shared_id), "peer_id": "123456789",
                "alias": '<img src=x onerror=alert(1)>',
                "note": "=1+1", "csrf": csrf_editor
            }, editor_cookie)[0] == 303
            status, body, _ = request("GET", f"/books?book={shared_id}", cookie=viewer_cookie)
            assert status == 200 and "&lt;img" in body and "<img" not in body
            assert 'href="rustdesk://connection/new/123456789"' in body
            assert "password=" not in body
            assert request("GET", f"/book/export.csv?book={shared_id}",
                           cookie=viewer_cookie)[0] == 200
            status, content, _ = request("GET", f"/book/export.csv?book={shared_id}",
                                         cookie=viewer_cookie)
            assert "'=1+1" in content
            status, content, _ = request("GET", f"/book/export.json?book={shared_id}",
                                         cookie=viewer_cookie)
            assert status == 200 and "123456789" in content
            with web.connect() as db:
                accounts.edit_user(db, admin["id"], viewer["id"], "viewer", 0)
            assert request("GET", "/books", cookie=viewer_cookie)[1].find(
                "Логин") >= 0, "disabled user retained session"
            assert request("POST", "/login", {
                "username": "viewer", "password": "viewer-password-123456"
            })[0] == 403
            print("PASS: migrated admin, role gates, personal/shared books, CRUD, CSV and sessions")
        finally:
            server.shutdown()
            server_thread.join(2)


if __name__ == "__main__":
    main()
