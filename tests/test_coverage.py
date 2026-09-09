"""
Tests for coverage.py: per-calendar-day movement of the weekly rate-limit windows.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover tests
"""
import datetime
import unittest
from zoneinfo import ZoneInfo

from usemeup import coverage

ET = ZoneInfo("America/New_York")
UTC = datetime.timezone.utc
RESET = datetime.datetime(2026, 9, 15, 23, 59, 59, tzinfo=UTC)


def s(t, pct, reset=RESET):
    return {"ts": t.isoformat(), "used_pct": pct, "resets_at": reset.isoformat()}


def et(y, m, d, h, mi=0):
    return datetime.datetime(y, m, d, h, mi, tzinfo=ET)


class RiseByDay(unittest.TestCase):
    def test_close_samples_land_on_their_calendar_day(self):
        a = [s(et(2026, 9, 8, 10, 0), 40), s(et(2026, 9, 8, 10, 5), 42), s(et(2026, 9, 8, 10, 10), 45)]
        r = coverage.rise_by_day(a, ET)
        self.assertEqual(list(r), ["2026-09-08"])
        self.assertAlmostEqual(r["2026-09-08"]["rise_pct"], 5.0)
        self.assertAlmostEqual(r["2026-09-08"]["measured_pct"], 5.0)
        self.assertAlmostEqual(r["2026-09-08"]["sampled_hours"], 0.2)

    def test_gap_across_midnight_is_split_by_time_and_marked_unmeasured(self):
        # 10 points accrue between 6 PM and 6 AM the next day with nothing sampling:
        # 6 of 12 hours on each side, so 5 each, none of it measured.
        a = [s(et(2026, 9, 8, 18, 0), 10), s(et(2026, 9, 9, 6, 0), 20)]
        r = coverage.rise_by_day(a, ET)
        self.assertAlmostEqual(r["2026-09-08"]["rise_pct"], 5.0)
        self.assertAlmostEqual(r["2026-09-09"]["rise_pct"], 5.0)
        self.assertEqual(r["2026-09-08"]["measured_pct"], 0.0)
        self.assertEqual(r["2026-09-08"]["sampled_hours"], 0.0)

    def test_reset_between_samples_counts_the_new_reading_not_a_fall(self):
        prev = RESET - datetime.timedelta(days=7)
        a = [s(et(2026, 9, 8, 19, 55), 57, reset=prev), s(et(2026, 9, 8, 20, 5), 1)]
        r = coverage.rise_by_day(a, ET)
        self.assertAlmostEqual(r["2026-09-08"]["rise_pct"], 1.0)

    def test_flat_samples_still_count_as_sampled_time(self):
        a = [s(et(2026, 9, 8, 9, 0), 40), s(et(2026, 9, 8, 9, 5), 40), s(et(2026, 9, 8, 9, 10), 40)]
        r = coverage.rise_by_day(a, ET)
        self.assertEqual(r["2026-09-08"]["rise_pct"], 0.0)
        self.assertAlmostEqual(r["2026-09-08"]["sampled_hours"], 0.2)

    def test_day_boundary_uses_the_given_zone_not_utc(self):
        # 11 PM to 11:10 PM Eastern is already the next day in UTC.
        a = [s(et(2026, 9, 8, 23, 0), 50), s(et(2026, 9, 8, 23, 10), 52)]
        r = coverage.rise_by_day(a, ET)
        self.assertEqual(list(r), ["2026-09-08"])


class Scope(unittest.TestCase):
    def test_scoped_window_name_yields_the_model(self):
        self.assertEqual(coverage.scope_of("7-day window, Fable only"), "Fable")
        self.assertIsNone(coverage.scope_of("7-day window, all models"))
        self.assertIsNone(coverage.scope_of("5-hour session window"))

    def test_build_keeps_weekly_windows_only(self):
        samples = {"session": [s(et(2026, 9, 8, 9, 0), 5)],
                   "weekly_scoped": [s(et(2026, 9, 8, 9, 0), 5), s(et(2026, 9, 8, 9, 5), 7)]}
        out = coverage.build(samples, {"weekly_scoped": "7-day window, Fable only"}, ET)
        self.assertEqual(list(out), ["weekly_scoped"])
        self.assertEqual(out["weekly_scoped"]["scope"], "Fable")
        self.assertAlmostEqual(out["weekly_scoped"]["days"]["2026-09-08"]["rise_pct"], 2.0)


if __name__ == "__main__":
    unittest.main()
