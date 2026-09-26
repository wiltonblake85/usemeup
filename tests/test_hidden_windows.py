"""
Tests for server.hidden_windows: the page honours the menu bar app's per-window
choice, read from ~/.usemeup/windows.json (written by WindowPrefs.swift).

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover tests
"""
import json
import os
import tempfile
import unittest

from usemeup import server


class HiddenWindows(unittest.TestCase):
    def write(self, body):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            f.write(body if isinstance(body, str) else json.dumps(body))
        self.addCleanup(os.remove, path)
        return path

    def test_only_off_hides(self):
        p = self.write({"windows": {"weekly_scoped": "off", "session": "whenNeeded",
                                    "weekly_all": "always"}})
        self.assertEqual(server.hidden_windows(p), ["weekly_scoped"])

    def test_no_file_hides_nothing(self):
        self.assertEqual(server.hidden_windows("/nonexistent/windows.json"), [])

    def test_a_broken_file_hides_nothing(self):
        self.assertEqual(server.hidden_windows(self.write("{not json")), [])
        self.assertEqual(server.hidden_windows(self.write({"windows": None})), [])


if __name__ == "__main__":
    unittest.main()
