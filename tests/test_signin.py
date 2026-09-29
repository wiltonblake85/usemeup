"""
Claude Code's sign-in: telling "signed out" apart from "no credential", not
retrying what cannot work, and warning before the sign-in ends.

What went wrong on 2026-09-29, and what each test below pins down:

  12:28 AM  the sign-in itself ended (refreshTokenExpiresAt)
   2:58 AM  the last access token ran out; readings stopped at 2:47 AM
   4:59 AM  a renewal was refused and Claude Code emptied both tokens
  all day   the page said "No Claude Code credential was found in the
            Keychain", which was false; the server ran `claude -p ok` every
            ten minutes, 56 times, each failing; the menu bar kept showing
            the 2:47 AM figures in full colour as if they were current

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover tests
"""
import datetime as _dt
import time
import unittest

from usemeup import panel, rate_limits, server, status

UTC = _dt.timezone.utc
NOW = _dt.datetime(2026, 9, 29, 19, 0, tzinfo=UTC)          # 3:00 PM ET
NOW_MS = NOW.timestamp() * 1000
HOUR_MS = 3600 * 1000
DAY_MS = 24 * HOUR_MS


def cred(access=True, refresh=True, expires_in_ms=6 * HOUR_MS, refresh_in_ms=29 * DAY_MS):
    """A Keychain blob shaped like Claude Code's, with fake token strings."""
    o = {"scopes": ["user:inference"], "subscriptionType": "max"}
    if access:
        o["accessToken"] = "fake-access"
    if refresh:
        o["refreshToken"] = "fake-refresh"
    if expires_in_ms is not None:
        o["expiresAt"] = NOW_MS + expires_in_ms
    if refresh_in_ms is not None:
        o["refreshTokenExpiresAt"] = NOW_MS + refresh_in_ms
    return {"claudeAiOauth": o, "mcpOAuth": {}}


def meta_of(blob):
    return rate_limits._meta_from(blob, "keychain:Claude Code-credentials", now_ms=NOW_MS)


class WhatTheCredentialSays(unittest.TestCase):
    def test_the_wiped_entry_of_that_morning_is_signed_out_not_missing(self):
        # Both tokens emptied, expiresAt gone, refreshTokenExpiresAt left behind.
        tok, source, meta = meta_of(cred(access=False, refresh=False, expires_in_ms=None,
                                         refresh_in_ms=-(18 * HOUR_MS)))
        self.assertIsNone(tok)
        self.assertTrue(rate_limits.signed_out(meta))
        self.assertFalse(meta.get("missing"))
        self.assertIn("signed out", meta["error"])
        self.assertNotIn("credential", meta["error"].lower())

    def test_a_healthy_sign_in_can_renew_and_is_not_signed_out(self):
        tok, _, meta = meta_of(cred())
        self.assertEqual(tok, "fake-access")
        self.assertTrue(meta["can_renew"])
        self.assertFalse(rate_limits.signed_out(meta))

    def test_an_expired_token_with_a_live_sign_in_is_renewable_not_signed_out(self):
        _, _, meta = meta_of(cred(expires_in_ms=-HOUR_MS))
        self.assertTrue(meta["expired"] and meta["can_renew"])
        self.assertFalse(rate_limits.signed_out(meta))

    def test_an_expired_token_after_the_sign_in_ended_is_signed_out(self):
        # 2:58 AM to 4:59 AM: both still present, neither usable.
        _, _, meta = meta_of(cred(expires_in_ms=-HOUR_MS, refresh_in_ms=-3 * HOUR_MS))
        self.assertTrue(rate_limits.signed_out(meta))

    def test_no_token_facts_ever_leave_as_values(self):
        _, _, meta = meta_of(cred())
        self.assertNotIn("fake-access", repr(meta))
        self.assertNotIn("fake-refresh", repr(meta))

    def test_timestamps_are_whole_seconds_for_the_app_to_parse(self):
        _, _, meta = meta_of(cred())
        self.assertNotIn(".", meta["refresh_expires_at"])

    def test_a_failed_keychain_call_is_not_mistaken_for_signed_out(self):
        self.assertFalse(rate_limits.signed_out({"error": "keychain call failed: OSError"}))
        self.assertIsNone(rate_limits.signin_summary({"error": "keychain call failed"}))


class SignInSummary(unittest.TestCase):
    def summary(self, blob):
        return rate_limits.signin_summary(meta_of(blob)[2], now=NOW)

    def test_a_month_out_is_ok(self):
        s = self.summary(cred())
        self.assertEqual(s["state"], "ok")
        self.assertAlmostEqual(s["seconds_left"], 29 * 86400, delta=2)

    def test_three_days_out_is_expiring(self):
        self.assertEqual(self.summary(cred(refresh_in_ms=3 * DAY_MS - 60000))["state"], "expiring")
        self.assertEqual(self.summary(cred(refresh_in_ms=3 * DAY_MS + 60000))["state"], "ok")

    def test_after_the_sign_in_ends_the_last_token_is_the_deadline(self):
        # 12:28 AM to 2:58 AM: readings still work, but not for long.
        s = self.summary(cred(expires_in_ms=2 * HOUR_MS, refresh_in_ms=-HOUR_MS))
        self.assertEqual(s["state"], "expiring")
        self.assertAlmostEqual(s["seconds_left"], 7200, delta=2)

    def test_signed_out_keeps_the_end_for_the_record(self):
        s = self.summary(cred(access=False, refresh=False, expires_in_ms=None,
                              refresh_in_ms=-(18 * HOUR_MS)))
        self.assertEqual(s["state"], "signed_out")
        self.assertTrue(s["ends_at"].startswith("2026-09-29"))

    def test_nothing_saved_is_missing(self):
        s = rate_limits.signin_summary({"missing": True, "can_renew": False}, now=NOW)
        self.assertEqual(s["state"], "missing")

    def test_no_end_date_is_unknown_not_a_warning(self):
        self.assertEqual(self.summary(cred(refresh_in_ms=None))["state"], "unknown")


class ProbeWhileSignedOut(unittest.TestCase):
    """The endpoint probe with the Keychain, the CLI and the network stubbed."""

    def setUp(self):
        self._saved = (rate_limits._token, rate_limits.refresh_via_claude_code,
                       rate_limits._call, dict(rate_limits._refresh_state))
        self.refreshes, self.calls = [], []
        rate_limits._call = lambda *a, **k: (self.calls.append(a) or (500, {}, "{}"))
        rate_limits._refresh_state.update(last=0.0, fails=0)

    def tearDown(self):
        (rate_limits._token, rate_limits.refresh_via_claude_code,
         rate_limits._call, st) = self._saved
        rate_limits._refresh_state.clear()
        rate_limits._refresh_state.update(st)

    def use(self, blob):
        rate_limits._token = lambda: rate_limits._meta_from(blob, "keychain:test", now_ms=NOW_MS)

    def test_signed_out_says_so_and_runs_nothing(self):
        self.use(cred(access=False, refresh=False, expires_in_ms=None, refresh_in_ms=-DAY_MS))
        rate_limits.refresh_via_claude_code = lambda: self.refreshes.append(1) or {"ran": True}
        d = rate_limits._probe_endpoint(auto_refresh=True)
        self.assertFalse(d["ok"])
        self.assertEqual(d["reason"], "signed_out")
        self.assertEqual(d["signin"]["state"], "signed_out")
        self.assertEqual(self.refreshes, [], "claude -p ok cannot help once the sign-in ended")
        self.assertEqual(self.calls, [], "no API call without a token")

    def test_a_renewable_expired_token_is_renewed_once_then_backs_off(self):
        self.use(cred(expires_in_ms=-HOUR_MS))
        rate_limits.refresh_via_claude_code = self._saved[1]    # the real gate
        ran = []

        class Failed:
            returncode, stdout, stderr = 1, "", "OAuth session expired"

        real_run = rate_limits.subprocess.run
        rate_limits.subprocess.run = lambda *a, **k: ran.append(a) or Failed()
        try:
            d1 = rate_limits._probe_endpoint(auto_refresh=True)
            d2 = rate_limits._probe_endpoint(auto_refresh=True)
        finally:
            rate_limits.subprocess.run = real_run
        self.assertEqual(len(ran), 1)
        self.assertEqual(d1["reason"], "token_expired")
        self.assertEqual(d2["refresh"]["ran"], False)
        self.assertEqual(rate_limits._refresh_state["fails"], 1)
        self.assertEqual(rate_limits.refresh_wait(), 1200)


class Backoff(unittest.TestCase):
    def setUp(self):
        self._st = dict(rate_limits._refresh_state)

    def tearDown(self):
        rate_limits._refresh_state.clear()
        rate_limits._refresh_state.update(self._st)

    def test_each_failure_doubles_the_wait_up_to_the_cap_and_success_resets_it(self):
        rate_limits._refresh_state.update(fails=0)
        waits = []
        for _ in range(7):
            waits.append(rate_limits.refresh_wait())
            rate_limits.note_refresh(False)
        self.assertEqual(waits, [600, 1200, 2400, 4800, 9600, 14400, 14400])
        rate_limits.note_refresh(True)
        self.assertEqual(rate_limits.refresh_wait(), 600)


def limits(signin_state, stale=False, stale_kind=None, stale_since=None, ends=None, ok=True):
    ends = ends or (NOW + _dt.timedelta(days=2)).isoformat()
    d = {"ok": ok, "windows": [], "checked_at": (stale_since or NOW.isoformat()),
         "signin": {"state": signin_state, "ends_at": ends, "seconds_left": None}}
    if stale:
        d.update(stale=True, stale_kind=stale_kind, stale_since=stale_since)
    return d


class WhatTheAppIsTold(unittest.TestCase):
    now = NOW.timestamp()

    def test_ok_says_nothing(self):
        v = panel.signin_view(limits("ok", ends=(NOW + _dt.timedelta(days=29)).isoformat()), self.now)
        self.assertIsNone(v["message"])
        self.assertIsNone(panel.signin_alert(v, self.now))

    def test_expiring_warns_once_per_sign_in(self):
        v = panel.signin_view(limits("expiring"), self.now)
        self.assertEqual(v["tone"], "watch")
        self.assertIn("in 2 days", v["message"])
        self.assertIn(" at ", v["ends_label"])
        a = panel.signin_alert(v, self.now)
        self.assertEqual(a["id"], "signin|2026-10-01|expiring")
        self.assertEqual(a["kind"], "signin")

    def test_signed_out_names_when_the_figures_stopped(self):
        since = (NOW - _dt.timedelta(hours=12)).isoformat()
        v = panel.signin_view(limits("signed_out", stale=True, stale_kind="signed_out",
                                     stale_since=since,
                                     ends=(NOW - _dt.timedelta(hours=18)).isoformat()), self.now)
        self.assertEqual(v["tone"], "alert")
        self.assertIn("stopped updating", v["message"])
        self.assertEqual(panel.signin_alert(v, self.now)["id"], "signin|2026-09-29|signed_out")

    def test_no_message_has_an_em_dash(self):
        for st in ("expiring", "signed_out", "missing"):
            v = panel.signin_view(limits(st), self.now)
            self.assertNotIn("—", v["message"])

    def test_the_status_line_source_has_no_sign_in_to_report(self):
        self.assertIsNone(panel.signin_view({"ok": True, "windows": []}, self.now))


class Live(unittest.TestCase):
    now = NOW.timestamp()

    def test_fresh_is_live(self):
        self.assertTrue(panel.is_live({"ok": True}, self.now))

    def test_signed_out_is_not_live_at_once(self):
        d = limits("signed_out", stale=True, stale_kind="signed_out",
                   stale_since=(NOW - _dt.timedelta(minutes=1)).isoformat())
        self.assertFalse(panel.is_live(d, self.now))

    def test_a_network_blip_keeps_its_colours_for_the_grace_period(self):
        mk = lambda mins: limits("ok", stale=True, stale_kind="no_data",
                                 stale_since=(NOW - _dt.timedelta(minutes=mins)).isoformat())
        self.assertTrue(panel.is_live(mk(5), self.now))
        self.assertFalse(panel.is_live(mk(25), self.now))


class MenuBarPayload(unittest.TestCase):
    def test_signed_out_with_no_reading_still_carries_the_sign_in(self):
        lim = {"ok": False, "reason": "signed_out", "error": "Claude Code is signed out.",
               "windows": [], "signin": {"state": "signed_out", "ends_at": None,
                                         "seconds_left": None}}
        m = panel.menubar({}, lim)
        self.assertFalse(m["ok"])
        self.assertEqual(m["signin"]["state"], "signed_out")
        self.assertEqual(m["alerts"][0]["kind"], "signin")
        self.assertFalse(m["live"])

    def test_status_file_carries_it_too(self):
        body = status.build({}, limits("expiring"))
        self.assertEqual(body["signin"]["state"], "expiring")
        self.assertIn("live", body)


class ServerTiming(unittest.TestCase):
    """How soon the server asks again, by what went wrong."""

    def setUp(self):
        self._saved = (rate_limits.probe, dict(server._cache), list(server._last_good))
        self.probes = 0
        server._last_good[0] = {"ok": True, "windows": [], "checked_at": NOW.isoformat()}

    def tearDown(self):
        rate_limits.probe, cache, lg = self._saved
        server._cache.clear()
        server._cache.update(cache)
        server._last_good[:] = lg

    def wire(self, reason):
        def probe():
            self.probes += 1
            return {"ok": False, "reason": reason, "error": reason, "windows": [],
                    "signin": {"state": "signed_out"} if reason == "signed_out" else None}
        rate_limits.probe = probe
        server._cache["limits"], server._cache["limits_at"] = None, 0

    def age(self, seconds):
        server._cache["limits_at"] = time.time() - seconds

    def test_signed_out_is_checked_again_within_a_minute(self):
        self.wire("signed_out")
        d = server._limits_unlocked()
        self.assertTrue(d["stale"])
        self.assertEqual(d["signin"]["state"], "signed_out")
        self.age(61)
        server._limits_unlocked()
        self.assertEqual(self.probes, 2)

    def test_a_failed_network_call_is_not_retried_every_minute(self):
        self.wire("no_data")
        server._limits_unlocked()
        self.age(61)
        server._limits_unlocked()
        self.assertEqual(self.probes, 1)
        self.age(301)
        server._limits_unlocked()
        self.assertEqual(self.probes, 2)

    def test_a_stale_rate_limit_keeps_its_back_off(self):
        self.wire("rate_limited")
        server._limits_unlocked()
        self.age(61)
        server._limits_unlocked()
        self.assertEqual(self.probes, 1)


if __name__ == "__main__":
    unittest.main()
