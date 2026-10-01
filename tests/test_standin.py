"""The token refresh finds `claude` without a shell's PATH, and a grey panel says why.

On 2026-09-30 the menu bar app's bundled server held the port for 15 hours with
PATH=/usr/bin:/bin:/usr/sbin:/sbin, auto-renew off, and nothing in the drop-down
but "Not live: these figures are from 4:01 PM".
"""
import os
import stat
import tempfile
import unittest

from usemeup import config, panel, rate_limits, status


def _fake_claude(folder):
    exe = os.path.join(folder, "claude")
    with open(exe, "w") as f:
        f.write("#!/bin/sh\necho ok\n")
    os.chmod(exe, os.stat(exe).st_mode | stat.S_IXUSR)
    return exe


class FindsClaude(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._dirs, self._path = config.CLI_DIRS, os.environ.get("PATH")
        self._run = rate_limits.subprocess.run
        self._state = dict(rate_limits._refresh_state)
        os.environ["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"

    def tearDown(self):
        config.CLI_DIRS = self._dirs
        os.environ["PATH"] = self._path or ""
        rate_limits.subprocess.run = self._run
        rate_limits._refresh_state.clear()
        rate_limits._refresh_state.update(self._state)

    def test_found_in_cli_dirs_with_launchd_path(self):
        exe = _fake_claude(self.tmp)
        config.CLI_DIRS = ["/nonexistent", self.tmp]
        self.assertEqual(rate_limits.claude_exe(), exe)

    def test_none_when_absent(self):
        config.CLI_DIRS = ["/nonexistent"]
        self.assertIsNone(rate_limits.claude_exe())

    def test_refresh_runs_the_full_path_with_its_folder_on_path(self):
        exe = _fake_claude(self.tmp)
        config.CLI_DIRS = [self.tmp]
        seen = {}

        class Out:
            returncode, stdout, stderr = 0, "ok", ""

        def run(cmd, **kw):
            seen["cmd"], seen["env"] = cmd, kw.get("env")
            return Out()

        rate_limits.subprocess.run = run
        rate_limits._refresh_state.update(last=0.0, fails=0)
        r = rate_limits.refresh_via_claude_code()
        self.assertTrue(r["ran"])
        self.assertEqual(seen["cmd"][0], exe)
        self.assertEqual(seen["cmd"][1:], rate_limits.REFRESH_ARGS)
        self.assertTrue(seen["env"]["PATH"].startswith(self.tmp + os.pathsep))

    def test_refresh_without_claude_says_where_it_looked(self):
        config.CLI_DIRS = ["/nonexistent"]
        rate_limits._refresh_state.update(last=0.0, fails=0)
        r = rate_limits.refresh_via_claude_code()
        self.assertFalse(r["ran"])
        self.assertIn("/nonexistent", r["reason"])

    def test_banner_says_whether_renewal_can_work(self):
        saved = config.AUTO_REFRESH
        try:
            config.AUTO_REFRESH = True
            config.CLI_DIRS = ["/nonexistent"]
            self.assertIn("WARNING: claude not found", config.banner())
            exe = _fake_claude(self.tmp)
            config.CLI_DIRS = [self.tmp]
            self.assertIn("claude found at %s" % exe, config.banner())
        finally:
            config.AUTO_REFRESH = saved

    def test_renewal_state(self):
        config.CLI_DIRS = ["/nonexistent"]
        self.assertEqual(rate_limits.renewal_state(False), "off")
        self.assertEqual(rate_limits.renewal_state(True), "no_cli")
        _fake_claude(self.tmp)
        config.CLI_DIRS = [self.tmp]
        self.assertEqual(rate_limits.renewal_state(True), "on")


def stale(kind, renewal=None, expires="2026-09-30T20:02:57+00:00"):
    return {"ok": True, "stale": True, "stale_kind": kind, "stale_renewal": renewal,
            "stale_since": "2026-09-30T20:01:57+00:00",
            "token": {"expires_at": expires}, "signin": {"state": "ok"}, "windows": []}


class SaysWhy(unittest.TestCase):
    def test_none_while_current(self):
        self.assertIsNone(panel.stale_why({"ok": True, "windows": []}))
        self.assertIsNone(panel.stale_why({}))

    def test_none_when_signin_explains_it(self):
        self.assertIsNone(panel.stale_why(stale("signed_out")))
        self.assertIsNone(panel.stale_why(stale("no_credential")))

    def test_token_expired_each_renewal_state(self):
        off = panel.stale_why(stale("token_expired", "off"))
        self.assertIn("access token ran out", off)
        self.assertIn("set not to renew it", off)
        self.assertIn("could not find the claude command",
                      panel.stale_why(stale("token_expired", "no_cli")))
        self.assertIn("has not worked yet", panel.stale_why(stale("token_expired", "on")))
        # An older server sends no renewal field: still a sentence.
        self.assertIn("has not worked yet", panel.stale_why(stale("token_expired")))

    def test_names_when_it_ran_out(self):
        s = panel.stale_why(stale("token_expired", "off"))
        self.assertRegex(s, r"ran out (at|on) .*\d:\d\d [AP]M")

    def test_no_expiry_still_reads(self):
        s = panel.stale_why(stale("token_expired", "no_cli", expires=None))
        self.assertTrue(s.startswith("Claude Code's access token ran out, and"))

    def test_other_failures(self):
        self.assertIn("waiting", panel.stale_why(stale("rate_limited")))
        self.assertEqual(panel.stale_why(stale("no_data")),
                         "UseMeUp's last check of your limits did not go through.")

    def test_no_em_dashes(self):
        for k, r in [("token_expired", "off"), ("token_expired", "no_cli"),
                     ("token_expired", "on"), ("rate_limited", None), ("no_data", None)]:
            self.assertNotIn("\u2014", panel.stale_why(stale(k, r)))

    def test_carried_by_menubar_and_status_json(self):
        lim = stale("token_expired", "no_cli")
        self.assertIn("claude command", panel.menubar({}, lim)["stale_why"])
        self.assertIn("claude command", status.build({}, lim)["stale_why"])
        self.assertIsNone(status.build({}, {"ok": True, "windows": []})["stale_why"])


if __name__ == "__main__":
    unittest.main()
