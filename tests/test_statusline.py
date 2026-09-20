"""
Tests for statusline.py: the credential-free rate-limit source.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover tests
"""
import datetime
import json
import os
import tempfile
import unittest

from usemeup import rate_limits, statusline

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 9, 20, 15, 0, tzinfo=UTC)


def epoch(dt):
    return int(dt.timestamp())


def doc(five=23.5, seven=41.2, now=NOW):
    rl = {}
    if five is not None:
        rl["five_hour"] = {"used_percentage": five,
                           "resets_at": epoch(now + datetime.timedelta(hours=2))}
    if seven is not None:
        rl["seven_day"] = {"used_percentage": seven,
                           "resets_at": epoch(now + datetime.timedelta(days=3))}
    return {"model": {"display_name": "x"}, "rate_limits": rl}


class Extract(unittest.TestCase):
    def test_maps_to_the_endpoint_keys_so_history_is_one_series(self):
        w = statusline.extract(doc())
        self.assertEqual(sorted(w), ["session", "weekly_all"])
        self.assertEqual(w["session"]["used_pct"], 23.5)
        self.assertTrue(w["weekly_all"]["resets_at"].endswith("+00:00"))

    def test_each_window_may_be_absent(self):
        self.assertEqual(sorted(statusline.extract(doc(five=None))), ["weekly_all"])
        self.assertEqual(statusline.extract({}), {})
        self.assertEqual(statusline.extract({"rate_limits": None}), {})

    def test_a_missing_percentage_is_skipped_not_recorded_as_zero(self):
        d = {"rate_limits": {"five_hour": {"resets_at": 1}}}
        self.assertEqual(statusline.extract(d), {})


class Capture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "statusline.json")

    def test_writes_the_drop_file_and_prints_a_line(self):
        out = statusline.capture(json.dumps(doc()), path=self.path)
        self.assertEqual(out, "5h 24% · 7d 41%")
        with open(self.path) as f:
            self.assertIn("weekly_all", json.load(f)["windows"])

    def test_a_session_with_no_rate_limits_yet_does_not_blank_a_good_file(self):
        statusline.capture(json.dumps(doc()), path=self.path)
        with open(self.path) as f:
            before = f.read()
        self.assertEqual(statusline.capture(json.dumps({"model": {}}), path=self.path), "")
        with open(self.path) as f:
            self.assertEqual(f.read(), before)

    def test_garbage_on_stdin_never_raises(self):
        self.assertEqual(statusline.capture("not json", path=self.path), "")
        self.assertEqual(statusline.capture("", path=self.path), "")

    def test_passthrough_keeps_an_existing_status_line(self):
        out = statusline.capture(json.dumps(doc()), passthrough="printf mine", path=self.path)
        self.assertEqual(out, "mine  5h 24% · 7d 41%")

    def test_a_broken_passthrough_still_prints_ours(self):
        out = statusline.capture(json.dumps(doc()), passthrough="exit 3", path=self.path)
        self.assertEqual(out, "5h 24% · 7d 41%")


class Read(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "statusline.json")

    def test_no_file_is_a_reason_not_an_exception(self):
        d = statusline.read(self.path, now=NOW)
        self.assertFalse(d["ok"])
        self.assertEqual(d["reason"], "no_statusline")

    def test_shape_matches_the_endpoint_windows(self):
        statusline.save(statusline.extract(doc()), self.path, now=NOW)
        d = statusline.read(self.path, now=NOW + datetime.timedelta(minutes=10))
        self.assertTrue(d["ok"])
        keys = set(rate_limits._shape_json({"limits": [
            {"kind": "session", "percent": 1, "resets_at": None}]})[0])
        for w in d["windows"]:
            self.assertEqual(set(w), keys)
        self.assertEqual([w["key"] for w in d["windows"]], ["session", "weekly_all"])
        self.assertEqual(d["windows"][0]["resets_in_seconds"], 2 * 3600 - 600)

    def test_the_sample_is_filed_under_capture_time_not_now(self):
        statusline.save(statusline.extract(doc()), self.path, now=NOW)
        d = statusline.read(self.path, now=NOW + datetime.timedelta(minutes=50))
        self.assertEqual(d["sample_ts"], NOW.isoformat())
        self.assertEqual(d["checked_at"], NOW.isoformat())
        self.assertEqual(d["age_seconds"], 3000)

    def test_a_window_that_has_since_reset_is_dropped(self):
        statusline.save(statusline.extract(doc()), self.path, now=NOW)
        d = statusline.read(self.path, now=NOW + datetime.timedelta(hours=3))
        self.assertEqual([w["key"] for w in d["windows"]], ["weekly_all"])

    def test_an_old_reading_is_refused_rather_than_shown_as_current(self):
        statusline.save(statusline.extract(doc()), self.path, now=NOW)
        d = statusline.read(self.path, now=NOW + datetime.timedelta(hours=7))
        self.assertFalse(d["ok"])
        self.assertEqual(d["reason"], "statusline_stale")

    def test_a_corrupt_file_never_raises(self):
        with open(self.path, "w") as f:
            f.write("{")
        self.assertFalse(statusline.read(self.path, now=NOW)["ok"])


class ProbeDispatch(unittest.TestCase):
    def setUp(self):
        self._read, self._ep = statusline.read, rate_limits._probe_endpoint
        self.calls = []

    def tearDown(self):
        statusline.read, rate_limits._probe_endpoint = self._read, self._ep

    def _wire(self, endpoint_ok, status_ok):
        def ep(*a, **k):
            self.calls.append("endpoint")
            return {"ok": endpoint_ok, "reason": None if endpoint_ok else "no_credential",
                    "error": "x", "windows": []}
        def rd(*a, **k):
            self.calls.append("statusline")
            return {"ok": status_ok, "windows": []}
        rate_limits._probe_endpoint, statusline.read = ep, rd

    def test_statusline_mode_never_touches_the_credential_path(self):
        self._wire(True, True)
        rate_limits.probe(source="statusline")
        self.assertEqual(self.calls, ["statusline"])

    def test_endpoint_mode_is_unchanged(self):
        self._wire(False, True)
        self.assertFalse(rate_limits.probe(source="endpoint")["ok"])
        self.assertEqual(self.calls, ["endpoint"])

    def test_auto_falls_back_and_says_so(self):
        self._wire(False, True)
        d = rate_limits.probe(source="auto")
        self.assertTrue(d["ok"])
        self.assertEqual(d["fallback_from"]["reason"], "no_credential")

    def test_auto_keeps_the_endpoint_failure_when_there_is_no_fallback(self):
        self._wire(False, False)
        self.assertEqual(rate_limits.probe(source="auto")["reason"], "no_credential")


if __name__ == "__main__":
    unittest.main()
