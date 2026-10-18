#!/usr/bin/env python3
"""Read-only RustDesk HBBS peer metadata exporter.

This process has no network and does not modify HBBS.
"""
import json
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote

SOURCE = Path(os.getenv("HBBS_DB", "/hbbs/db_v2.sqlite3"))
DEST = Path(os.getenv("EXPORT_PATH", "/out/peers.json"))
INTERVAL = max(30, int(os.getenv("SYNC_SECONDS", "60")))


def export_once():
    stamp = int(time.time())
    if not SOURCE.is_file():
        return {"ok": False, "error": f"База HBBS не найдена: {SOURCE}", "devices": [], "timestamp": stamp}

    uri = f'file:{quote(str(SOURCE), safe="/")}?mode=ro'
    with sqlite3.connect(uri, uri=True, timeout=5) as conn:
        conn.execute("PRAGMA query_only=ON")
        fields = {r[1] for r in conn.execute('PRAGMA table_info("peer")')}
        if "id" not in fields:
            return {"ok": False, "error": 'Нет таблицы peer или поля id', "devices": [], "timestamp": stamp}

        columns = ["id"] + [key for key in ("info", "created_at") if key in fields]
        rows = conn.execute(
            "SELECT " + ", ".join('"' + key + '"' for key in columns) +
            " FROM peer LIMIT 10000"
        )
        devices = []
        for values in rows:
            entry = dict(zip(columns, values))
            device_id = entry.get("id")
            if not isinstance(device_id, str) or not (1 <= len(device_id) <= 128):
                continue
            details = {}
            try:
                loaded = json.loads(entry.get("info") or "{}")
                if isinstance(loaded, dict):
                    details = loaded
            except (TypeError, ValueError):
                pass

            devices.append({
                "id": device_id,
                "device_name": str(details.get("device_name") or "")[:120],
                "os": str(details.get("os") or "")[:100],
                "username": str(details.get("username") or "")[:120],
                "created_at": str(entry.get("created_at") or "")[:64],
            })
        return {"ok": True, "error": "", "devices": devices, "timestamp": stamp}


def main():
    DEST.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            result = export_once()
        except (OSError, sqlite3.Error) as exc:
            result = {
                "ok": False, "error": f"{type(exc).__name__}: {exc}",
                "devices": [], "timestamp": int(time.time())
            }

        if not result["ok"] and DEST.is_file():
            try:
                old = json.loads(DEST.read_text("utf-8"))
                if isinstance(old.get("devices"), list):
                    result["devices"] = old["devices"]
                    result["stale"] = True
            except (OSError, ValueError, TypeError):
                pass

        temporary = DEST.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        os.chmod(temporary, 0o640)
        os.replace(temporary, DEST)
        print(f'HBBS metadata: ok={result["ok"]} devices={len(result["devices"])}', flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
