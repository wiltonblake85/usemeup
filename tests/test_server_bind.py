"""
Tests for how the server claims its port (server.bind, server.occupant, /api/ping).

The bug these guard against, found 2026-09-25: the LaunchAgent scanned
transcripts and started its sampler BEFORE binding, lost the port to the menu
bar app's bundled server, exited, and was restarted by launchd every ten
seconds, 3,737 times. A server that finds its port taken must now wait,
quietly, and do no work until it has the port.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover tests
"""
import json
import socket
import threading
import time
import unittest
import urllib.request

from usemeup import config
from usemeup import server


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def hold(port):
    """A plain listener on the port that never speaks HTTP: 'another program'."""
    s = socket.socket()
    s.bind(("127.0.0.1", port))
    s.listen(5)
    return s


def serve_in_thread(httpd):
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return t


class Bind(unittest.TestCase):
    def test_binds_at_once_when_the_port_is_free(self):
        port = free_port()
        logs = []
        httpd = server.bind(port, retry_every=0.05, log=logs.append)
        try:
            self.assertEqual(httpd.server_address[1], port)
            self.assertEqual(logs, [])          # nothing to say when nothing waited
        finally:
            httpd.server_close()

    def test_waits_while_taken_then_takes_over(self):
        port = free_port()
        holder = hold(port)
        logs, got = [], {}

        def attempt():
            got["httpd"] = server.bind(port, retry_every=0.05, log=logs.append)

        t = threading.Thread(target=attempt, daemon=True)
        t.start()
        time.sleep(0.4)                          # several retries happen in here
        self.assertTrue(t.is_alive(), "bind returned while the port was still taken")
        holder.close()
        t.join(3)
        self.assertFalse(t.is_alive(), "bind never took the freed port")
        try:
            self.assertEqual(got["httpd"].server_address[1], port)
            # One line for the whole wait, not one per retry.
            self.assertEqual(len(logs), 1, logs)
            self.assertIn("another program", logs[0])
            self.assertIn("standing by", logs[0])
        finally:
            got["httpd"].server_close()

    def test_stop_ends_a_standby(self):
        port = free_port()
        holder = hold(port)
        stop = threading.Event()
        out = {}
        t = threading.Thread(target=lambda: out.setdefault(
            "r", server.bind(port, retry_every=0.05, log=lambda m: None, stop=stop)),
            daemon=True)
        t.start()
        time.sleep(0.15)
        stop.set()
        t.join(2)
        holder.close()
        self.assertFalse(t.is_alive())
        self.assertIsNone(out["r"])

    def test_other_bind_errors_are_not_swallowed(self):
        # A bad address is not "somebody else has it"; standby would hide it forever.
        orig = server.Server

        def explode(*a, **k):
            raise OSError(13, "Permission denied")

        server.Server = explode
        try:
            with self.assertRaises(OSError):
                server.bind(free_port(), retry_every=0.05, log=lambda m: None)
        finally:
            server.Server = orig


class RunOrder(unittest.TestCase):
    def test_a_server_that_does_not_have_the_port_does_no_work(self):
        # The heart of the 2026-09-25 fix: no scan and no sampler until the
        # port is ours. Before, both ran first and the bind failed after.
        port = free_port()
        holder = hold(port)
        calls = []
        saved = (server._maybe_ingest, server.start_sampler, server.BIND_RETRY)
        server._maybe_ingest = lambda force=False: calls.append("ingest") or {"error": "stub"}
        server.start_sampler = lambda: calls.append("sampler")
        server.BIND_RETRY = 0.05
        try:
            t = threading.Thread(target=server.run, args=(port, False), daemon=True)
            t.start()
            time.sleep(0.4)
            self.assertEqual(calls, [], "scanned or sampled without the port")
            holder.close()
            deadline = time.time() + 3
            while len(calls) < 2 and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(calls, ["ingest", "sampler"])
            # Serving already: ping answers.
            self.assertIn("UseMeUp", server.occupant(port))
        finally:
            server._maybe_ingest, server.start_sampler, server.BIND_RETRY = saved


class Occupant(unittest.TestCase):
    def test_names_another_usemeup_by_pid(self):
        port = free_port()
        httpd = server.bind(port, retry_every=0.05, log=lambda m: None)
        serve_in_thread(httpd)
        try:
            who = server.occupant(port)
            self.assertIn("UseMeUp", who)
            self.assertIn("(pid ", who)
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_a_silent_listener_is_another_program(self):
        port = free_port()
        holder = hold(port)
        try:
            self.assertEqual(server.occupant(port, timeout=0.3), "another program")
        finally:
            holder.close()

    def test_nothing_listening_is_another_program_not_a_crash(self):
        self.assertEqual(server.occupant(free_port(), timeout=0.3), "another program")


class Ping(unittest.TestCase):
    def test_ping_says_who_and_how(self):
        port = free_port()
        httpd = server.bind(port, retry_every=0.05, log=lambda m: None)
        serve_in_thread(httpd)
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open("http://127.0.0.1:%d/api/ping" % port, timeout=2) as r:
                d = json.loads(r.read())
            self.assertEqual(d["app"], config.APP)
            self.assertIn(d["source"], config.SOURCES)
            self.assertIsInstance(d["pid"], int)
            self.assertIsInstance(d["auto_refresh"], bool)
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
