#!/usr/bin/env python3
"""Periodic read-only RustDesk HBBS OnlineRequest polling.

No privileged container, secrets, database writes or HBBS configuration changes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time

from status_probe import probe_online

PEERS = Path(os.getenv("PEERS_JSON", "/data/peers.json"))
OUTPUT = Path(os.getenv("STATUS_JSON", "/data/statuses.json"))
HOST = os.getenv("HBBS_STATUS_HOST", "")
PORT = int(os.getenv("HBBS_STATUS_PORT", "21115"))
INTERVAL = max(10, int(os.getenv("STATUS_INTERVAL", "20")))
EXPORT_MAX_AGE = 180


def poll_once(peers_file=PEERS, host=HOST, port=PORT, now=None):
    """An HBBS error or stale export is UNKNOWN, never an offline result."""
    stamp = int(time.time() if now is None else now)
    base = {"ok": False, "timestamp": stamp, "devices": {}, "error": ""}
    try:
        if not host:
            raise ValueError("HBBS_STATUS_HOST не настроен")
        data = json.loads(Path(peers_file).read_text("utf-8"))
        exported_at = data.get("timestamp")
        if (data.get("ok") is not True or data.get("stale") is True
                or not isinstance(exported_at, int)
                or not 0 <= stamp - exported_at <= EXPORT_MAX_AGE):
            raise ValueError("Экспорт HBBS недоступен или устарел")
        entries = data.get("devices")
        if not isinstance(entries, list):
            raise ValueError("Некорректный реестр HBBS")
        ids = list(dict.fromkeys(item["id"] for item in entries
                                  if isinstance(item, dict)
                                  and isinstance(item.get("id"), str)
                                  and 1 <= len(item["id"]) <= 128))
        if len(ids) > 10000:
            raise ValueError("Слишком много записей")
        if ids:
            states = probe_online(host, port, ids, timeout=3.0)
        else:
            states = {}
        if len(states) != len(ids):
            raise ValueError("Неполный ответ HBBS")
        base["ok"] = True
        base["devices"] = {key: ("online" if val else "offline") for key, val in states.items()}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Keep the message short, no machine IDs or credentials in logs.
        base["error"] = str(exc)[:160]
    return base


def write_atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o640)
    os.replace(tmp, path)


def main():
    while True:
        result = poll_once()
        try:
            write_atomic(OUTPUT, result)
        except OSError as exc:
            print(f"Status export error: {type(exc).__name__}: {exc}", flush=True)
        print(f'HBBS online probe: ok={result["ok"]} '
              f'online={sum(v == "online" for v in result["devices"].values())} '
              f'count={len(result["devices"])}', flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
