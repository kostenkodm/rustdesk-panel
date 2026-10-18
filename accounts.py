"""Users and server-side address books for RustDesk Panel.

Panel roles do NOT enforce RustDesk connection permissions. No writes to HBBS.
Requires only stdlib and the existing panel SQLite connection.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time

ROLES = ("admin", "editor", "viewer")
ITERATIONS = 600_000


class BadInput(ValueError):
    pass


class AccessDenied(PermissionError):
    pass


def hash_password(password):
    if not isinstance(password, str) or not (14 <= len(password) <= 256):
        raise BadInput("Пароль: от 14 до 256 символов")
    salt = secrets.token_bytes(24)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return salt, digest


def verify_password(row, password):
    if not isinstance(password, str) or not (1 <= len(password) <= 500):
        return False
    test = hashlib.pbkdf2_hmac("sha256", password.encode(), row["salt"], ITERATIONS)
    return hmac.compare_digest(row["digest"], test)


def schema(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL COLLATE NOCASE UNIQUE,
        salt BLOB NOT NULL,
        digest BLOB NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('admin','editor','viewer')),
        active INTEGER NOT NULL DEFAULT 1,
        created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS address_books (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        owner_user_id INTEGER REFERENCES users(id),
        created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id INTEGER NOT NULL REFERENCES address_books(id),
        peer_id TEXT NOT NULL,
        alias TEXT NOT NULL DEFAULT '',
        tags TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        updated_at INTEGER NOT NULL,
        UNIQUE(book_id, peer_id)
    );
    CREATE INDEX IF NOT EXISTS ix_contacts_book ON contacts(book_id);
    """)
    session_cols = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
    if "user_id" not in session_cols:
        db.execute("ALTER TABLE sessions ADD COLUMN user_id INTEGER REFERENCES users(id)")
    admin = db.execute("SELECT id FROM users WHERE username='admin' COLLATE NOCASE").fetchone()
    if not admin:
        legacy = db.execute("SELECT salt,digest FROM admin WHERE id=1").fetchone()
        if not legacy:
            raise RuntimeError("Legacy administrator account not initialized")
        db.execute(
            "INSERT INTO users(username,salt,digest,role,active,created_at) VALUES (?,?,?,?,?,?)",
            ("admin", legacy["salt"], legacy["digest"], "admin", 1, int(time.time())),
        )
    # Never try to interpret old v0.3 cookies as a different user's session.
    db.execute("DELETE FROM sessions WHERE user_id IS NULL")
    shared = db.execute(
        "SELECT id FROM address_books WHERE name=? AND owner_user_id IS NULL",
        ("Общая адресная книга",),
    ).fetchone()
    if not shared:
        cursor = db.execute(
            "INSERT INTO address_books(name,owner_user_id,created_at) VALUES (?,NULL,?)",
            ("Общая адресная книга", int(time.time())),
        )
        shared_id = cursor.lastrowid
        # The previous catalog was already accessible to everyone with the
        # single admin password: migrate only its explicitly saved cards.
        for item in db.execute("SELECT id,alias,group_name,note FROM devices"):
            db.execute(
                "INSERT OR IGNORE INTO contacts(book_id,peer_id,alias,tags,note,updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (shared_id, item["id"], item["alias"], item["group_name"],
                 item["note"], int(time.time())),
            )


def authenticate(db, username, password):
    if not isinstance(username, str) or len(username) > 64:
        return None
    row = db.execute(
        "SELECT id,username,salt,digest,role,active FROM users WHERE username=?",
        (username.strip(),),
    ).fetchone()
    if not row or not row["active"]:
        # Hash anyway to reduce distinguishable timings.
        hashlib.pbkdf2_hmac("sha256", str(password)[:500].encode(), b"\0"*24, ITERATIONS)
        return None
    if verify_password(row, password):
        return {key: row[key] for key in ("id", "username", "role", "active")}
    return None


def user_by_id(db, user_id):
    row = db.execute(
        "SELECT id,username,role,active FROM users WHERE id=? AND active=1",
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


def user_listing(db):
    return [dict(row) for row in db.execute(
        "SELECT id,username,role,active,created_at FROM users ORDER BY username COLLATE NOCASE"
    )]


def new_user(db, username, password, role):
    username = str(username).strip()
    if not (3 <= len(username) <= 64) or not all(
            c.isascii() and (c.isalnum() or c in "._-") for c in username):
        raise BadInput("Логин: 3–64 символа, латиница, цифры, точка, дефис или _")
    if role not in ROLES:
        raise BadInput("Неизвестная роль")
    salt, digest = hash_password(password)
    try:
        db.execute(
            "INSERT INTO users(username,salt,digest,role,active,created_at) "
            "VALUES (?,?,?,?,1,?)",
            (username, salt, digest, role, int(time.time())),
        )
    except sqlite3.IntegrityError as exc:
        raise BadInput("Такой логин уже существует") from exc


def edit_user(db, actor_id, target_id, role, active, new_password=""):
    target = db.execute("SELECT id,role,active FROM users WHERE id=?", (target_id,)).fetchone()
    if not target or role not in ROLES or active not in (0, 1):
        raise BadInput("Некорректные данные пользователя")
    if int(target_id) == int(actor_id) and (role != "admin" or not active):
        raise BadInput("Нельзя отключить собственную учётную запись администратора")
    if target["role"] == "admin" and target["active"] and (role != "admin" or not active):
        admin_count = db.execute(
            "SELECT COUNT(*) FROM users WHERE role='admin' AND active=1"
        ).fetchone()[0]
        if admin_count <= 1:
            raise BadInput("Нельзя отключить последнего администратора")
    if new_password:
        salt, digest = hash_password(new_password)
        db.execute("UPDATE users SET role=?,active=?,salt=?,digest=? WHERE id=?",
                   (role, active, salt, digest, target_id))
    else:
        db.execute("UPDATE users SET role=?,active=? WHERE id=?",
                   (role, active, target_id))
    if not active or new_password or role != target["role"]:
        db.execute("DELETE FROM sessions WHERE user_id=?", (target_id,))


def books_visible(db, user):
    return [dict(row) for row in db.execute(
        "SELECT id,name,owner_user_id FROM address_books "
        "WHERE owner_user_id IS NULL OR owner_user_id=? ORDER BY owner_user_id IS NOT NULL, name",
        (user["id"],),
    )]


def book_access(db, user, book_id, edit=False):
    row = db.execute(
        "SELECT id,name,owner_user_id FROM address_books WHERE id=? AND "
        "(owner_user_id IS NULL OR owner_user_id=?)",
        (book_id, user["id"]),
    ).fetchone()
    if row is None:
        raise AccessDenied("Адресная книга недоступна")
    if edit and user["role"] == "viewer":
        raise AccessDenied("Для изменения необходима роль редактора")
    return dict(row)


def create_book(db, user, name, personal=False):
    name = str(name).strip()
    if not (1 <= len(name) <= 80):
        raise BadInput("Название книги: 1–80 символов")
    if user["role"] == "viewer":
        raise AccessDenied("Недостаточно прав")
    if not personal and user["role"] != "admin":
        raise AccessDenied("Создавать общие книги может только администратор")
    owner = user["id"] if personal else None
    db.execute(
        "INSERT INTO address_books(name,owner_user_id,created_at) VALUES (?,?,?)",
        (name, owner, int(time.time())),
    )


def contacts_in_book(db, book_id):
    return [dict(row) for row in db.execute(
        "SELECT id,book_id,peer_id,alias,tags,note,updated_at FROM contacts "
        "WHERE book_id=? ORDER BY alias COLLATE NOCASE, peer_id", (book_id,),
    )]


def save_contact(db, book_id, peer_id, alias, tags, note):
    peer_id = str(peer_id).strip()
    alias, tags, note = str(alias).strip(), str(tags).strip(), str(note).strip()
    if (not (1 <= len(peer_id) <= 128)
            or any(ord(c) < 32 for c in peer_id)
            or len(alias) > 120 or len(tags) > 200 or len(note) > 1200):
        raise BadInput("Некорректные данные контакта")
    db.execute(
        """INSERT INTO contacts(book_id,peer_id,alias,tags,note,updated_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(book_id,peer_id) DO UPDATE SET
           alias=excluded.alias,tags=excluded.tags,note=excluded.note,
           updated_at=excluded.updated_at""",
        (book_id, peer_id, alias, tags, note, int(time.time())),
    )


def delete_contact(db, book_id, peer_id):
    db.execute("DELETE FROM contacts WHERE book_id=? AND peer_id=?",
               (book_id, peer_id))


def import_hbbs(db, book_id, peers):
    if not isinstance(peers, dict):
        raise BadInput("Неверные данные HBBS")
    count = 0
    for peer_id, info in peers.items():
        if not isinstance(info, dict):
            continue
        if not (1 <= len(peer_id) <= 128) or any(ord(c) < 32 for c in peer_id):
            continue
        db.execute(
            "INSERT OR IGNORE INTO contacts(book_id,peer_id,alias,tags,note,updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (book_id, peer_id, str(info.get("device_name") or "")[:120], "", "",
             int(time.time())),
        )
        count += 1
    return count
