#!/usr/bin/env python3
"""Read-only online presence probe for unmodified RustDesk OSS HBBS.

Uses the public RendezvousMessage OnlineRequest/OnlineResponse protocol over
the HBBS NAT-test TCP port (21115), not a database timestamp or TCP ping.
No dependencies beyond Python stdlib. No changes to HBBS, HBBS DB or keys.

Reference: rustdesk/hbb_common/protos/rendezvous.proto;
rustdesk/rustdesk-server/src/rendezvous_server.rs::handle_listener2.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import struct
import sys

DEFAULT_PORT = 21115
MAX_FRAME = 65536
BATCH_SIZE = 200


class ProtocolError(ValueError):
    """Received a malformed or unexpected RustDesk reply."""


def varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("negative protobuf varint")
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def wire_bytes(number: int, data: bytes) -> bytes:
    return varint((number << 3) | 2) + varint(len(data)) + data


def parse_varint(data: bytes, start: int) -> tuple[int, int]:
    value = 0
    for step in range(10):
        pos = start + step
        if pos >= len(data):
            raise ProtocolError("truncated protobuf varint")
        byte = data[pos]
        value |= (byte & 0x7F) << (7 * step)
        if byte < 0x80:
            return value, pos + 1
    raise ProtocolError("oversized protobuf varint")


def fields(data: bytes):
    offset = 0
    while offset < len(data):
        tag, offset = parse_varint(data, offset)
        number, kind = tag >> 3, tag & 7
        if not number:
            raise ProtocolError("invalid protobuf field number")
        if kind == 2:
            length, offset = parse_varint(data, offset)
            if length > MAX_FRAME or offset + length > len(data):
                raise ProtocolError("invalid protobuf field length")
            payload = data[offset:offset+length]
            offset += length
            yield number, kind, payload
        elif kind == 0:
            _, offset = parse_varint(data, offset)
        elif kind in (1, 5):
            offset += 8 if kind == 1 else 4
            if offset > len(data):
                raise ProtocolError("truncated protobuf fixed field")
        else:
            raise ProtocolError("unsupported protobuf wire type")


def encode_request(peer_ids: list[str]) -> bytes:
    # OnlineRequest { repeated string peers = 2; }
    submessage = b"".join(wire_bytes(2, peer.encode("utf-8")) for peer in peer_ids)
    # RendezvousMessage { OnlineRequest online_request = 23; }
    return wire_bytes(23, submessage)


def unpack_response(payload: bytes, count: int) -> list[bool]:
    # RendezvousMessage { OnlineResponse online_response = 24; }
    responses = [value for field, kind, value in fields(payload) if field == 24 and kind == 2]
    if len(responses) != 1:
        raise ProtocolError("OnlineResponse field not found or duplicated")
    masks = [value for field, kind, value in fields(responses[0]) if field == 1 and kind == 2]
    if len(masks) != 1:
        raise ProtocolError("OnlineResponse.states field not found or duplicated")
    states = masks[0]
    if len(states) != (count + 7) // 8:
        raise ProtocolError("unexpected number of status bytes")
    # hbbs writes bit 7-i%8 (MSB first) to each states byte.
    return [bool(states[i // 8] & (1 << (7 - i % 8))) for i in range(count)]


def frame(data: bytes) -> bytes:
    length = len(data)
    if length <= 63:
        header = bytes([(length << 2)])
    elif length <= 0x3FFF:
        header = struct.pack("<H", (length << 2) | 1)
    elif length <= 0x3FFFFF:
        header = ((length << 2) | 2).to_bytes(3, "little")
    elif length <= 0x3FFFFFFF:
        header = struct.pack("<I", (length << 2) | 3)
    else:
        raise ValueError("RustDesk frame too large")
    return header + data


def read_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        part = sock.recv(size - len(chunks))
        if not part:
            raise ProtocolError("peer disconnected before replying")
        chunks.extend(part)
    return bytes(chunks)


def recv_frame(sock: socket.socket) -> bytes:
    first = read_exact(sock, 1)[0]
    head_len = (first & 0x3) + 1
    header = bytes([first]) + read_exact(sock, head_len - 1)
    length = int.from_bytes(header, "little") >> 2
    if length > MAX_FRAME:
        raise ProtocolError("unreasonably large RustDesk reply")
    return read_exact(sock, length)


def probe_online(host: str, port: int, peer_ids: list[str], timeout: float = 3.0) -> dict[str, bool]:
    """Return a verified per-ID snapshot; raise on network/protocol failure.

    Never treat a failed query as offline. Only returns offline when the server
    actually responds with the corresponding bit cleared.
    """
    if not host or not (1 <= port <= 65535):
        raise ValueError("invalid host or port")
    if not (0 < timeout <= 10):
        raise ValueError("invalid timeout")
    if len(peer_ids) > 10000:
        raise ValueError("too many peers")
    if any(not isinstance(item, str) or not (1 <= len(item.encode("utf-8")) <= 128)
           for item in peer_ids):
        raise ValueError("invalid peer ID")

    result = {}
    for offset in range(0, len(peer_ids), BATCH_SIZE):
        batch = peer_ids[offset:offset + BATCH_SIZE]
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(frame(encode_request(batch)))
            online = unpack_response(recv_frame(sock), len(batch))
        result.update(zip(batch, online))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only RustDesk OSS online probe")
    parser.add_argument("--host", required=True, help="HBBS host IP (not 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--peers-json", type=Path, required=True, help="exporter's peers.json")
    parser.add_argument("--show-ids", action="store_true", help="print device IDs and their states")
    opts = parser.parse_args(argv)
    try:
        peers = json.loads(opts.peers_json.read_text("utf-8"))
        if not peers.get("ok", False) or peers.get("stale", False):
            raise ValueError("HBBS export is stale or failed; unable to claim exact counts")
        ids = list(dict.fromkeys(x["id"] for x in peers["devices"]
                                  if isinstance(x, dict) and isinstance(x.get("id"), str)))
        if not ids:
            raise ValueError("export has no RustDesk IDs")
        statuses = probe_online(opts.host, opts.port, ids)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"UNKNOWN: could not query HBBS online status: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    online = sum(statuses.values())
    print(f"HBBS: total={len(statuses)} online={online} offline={len(statuses) - online}")
    if opts.show_ids:
        for peer_id, active in sorted(statuses.items(), key=lambda row: row[0].casefold()):
            print(("ONLINE " if active else "OFFLINE") + peer_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
