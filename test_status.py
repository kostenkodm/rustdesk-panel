"""Offline protocol tests for RustDesk OSS presence query.

Run: python3 test_status.py
Mocks a TCP server; never connects to a real HBBS.
"""
import socketserver
import threading
import unittest

import status_probe as sp


class OnlineHandler(socketserver.BaseRequestHandler):
    def handle(self):
        request = sp.recv_frame(self.request)
        outer = [value for number, kind, value in sp.fields(request)
                 if number == 23 and kind == 2]
        assert len(outer) == 1, "unexpected request"
        peers = [value.decode("utf-8") for number, kind, value in sp.fields(outer[0])
                 if number == 2 and kind == 2]
        response = bytearray((len(peers) + 7) // 8)
        for i, peer in enumerate(peers):
            if peer.endswith("0") or peer.endswith("2"):
                response[i // 8] |= 1 << (7 - (i % 8))
        self.request.sendall(sp.frame(sp.wire_bytes(24, sp.wire_bytes(1, bytes(response)))))


class TestProtocol(unittest.TestCase):
    def test_frame_lengths_and_varints(self):
        for length in (0, 1, 63, 64, 255, 16383, 16384):
            class Buffer:
                def __init__(self, content):
                    self.content = content
                def recv(self, n):
                    out, self.content = self.content[:n], self.content[n:]
                    return out
            payload = bytes([33]) * length
            assert sp.recv_frame(Buffer(sp.frame(payload))) == payload
        for n in (0, 127, 128, 999, 65535, (24 << 3) | 2):
            self.assertEqual(sp.parse_varint(sp.varint(n), 0)[0], n)

    def test_request_protobuf(self):
        request = sp.encode_request(["123", "abc"])
        wrapper = [(n, val) for n, kind, val in sp.fields(request)]
        self.assertEqual(wrapper[0][0], 23)
        inner = [(n, val.decode()) for n, kind, val in sp.fields(wrapper[0][1])]
        self.assertEqual(inner, [(2, "123"), (2, "abc")])

    def test_status_bit_order_and_batch(self):
        with socketserver.TCPServer(("127.0.0.1", 0), OnlineHandler) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            ids = [f"test-{i % 10}" + f"-{i}" for i in range(205)]
            # Explicit bit order: first, third and tenth digit suffix only.
            ids = [f"test-{i}-{i % 10}" for i in range(205)]
            statuses = sp.probe_online("127.0.0.1", server.server_address[1], ids)
            self.assertEqual(len(statuses), 205)
            for item in ids:
                self.assertEqual(statuses[item], item.endswith(("0", "2")))
            server.shutdown()
            worker.join(2)

    def test_bad_reply_not_offline(self):
        with self.assertRaises(sp.ProtocolError):
            sp.unpack_response(b"", 1)
        with self.assertRaises(sp.ProtocolError):
            sp.unpack_response(sp.wire_bytes(24, b""), 8)
        with self.assertRaises(sp.ProtocolError):
            sp.unpack_response(sp.wire_bytes(24, sp.wire_bytes(1, b"\0")), 23)
        with self.assertRaises(ValueError):
            sp.probe_online("127.0.0.1", 0, ["test"])
        with self.assertRaises(ValueError):
            sp.probe_online("127.0.0.1", 21115, [""])


if __name__ == "__main__":
    unittest.main(verbosity=2)
