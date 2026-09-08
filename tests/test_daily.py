"""
Tests for daily.py: the weekly window as seven buckets.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover tests
"""
import datetime
import unittest

from usemeup import daily

UTC = datetime.timezone.utc
# A week that resets Tuesday 2026-09-08 23:59:59Z (7:59:59 PM Eastern).
RESET = datetime.datetime(2026, 9, 8, 23, 59, 59, tzinfo=UTC)
START = RESET - datetime.timedelta(days=7)
WEEK_S = 7 * 86400


def window(used, now_offset_hours=0):
    return {"key": "weekly_all", "used_pct": used, "window_seconds": WEEK_S,
            "resets_at": RESET.isoformat()}


def sample(t, pct, reset=RESET):
    return {"ts": t.isoformat(), "used_pct": pct, "resets_at": reset.isoformat()}


def at(day, hours=0.0):
    """A moment `hours` into bucket `day` (0 = first day of the week)."""
    return START + datetime.timedelta(days=day, hours=hours)


class Buckets(unittest.TestCase):
    def test_seven_buckets_end_at_reset(self):
        d = daily.build(window(10), [sample(at(0, 1), 0), sample(at(0, 1.1), 10)], now=at(0, 2))
        week = d["weeks"][0]
        self.assertEqual(len(week["days"]), 7)
        self.assertEqual(week["days"][0]["start"], START.isoformat())
        self.assertEqual(week["days"][6]["end"], RESET.isoformat())
        self.assertEqual(d["allotment_pct"], 14.29)

    def test_not_a_weekly_window(self):
        self.assertIsNone(daily.build({"window_seconds": 18000, "used_pct": 5,
                                       "resets_at": RESET.isoformat()}, []))

    def test_status_past_today_future(self):
        d = daily.build(window(0), [], now=at(2, 5))
        st = [x["status"] for x in d["weeks"][0]["days"]]
        self.assertEqual(st, ["past", "past", "today", "future", "future", "future", "future"])


class Attribution(unittest.TestCase):
    def test_close_samples_are_measured_in_their_bucket(self):
        s = [sample(at(1, 3), 4), sample(at(1, 3.1), 6), sample(at(1, 3.2), 9)]
        d = daily.build(window(9), s, now=at(1, 3.3))
        day1 = d["weeks"][0]["days"][1]
        self.assertAlmostEqual(day1["measured_pct"], 5.0)   # 4 -> 9 within measured spacing
        # The 4% that existed before the first sample is a gap from window start.
        self.assertAlmostEqual(day1["estimated_pct"] + d["weeks"][0]["days"][0]["estimated_pct"], 4.0)

    def test_gap_spreads_by_time_without_weights(self):
        # 0% at day 1 noon, 12% at day 3 noon: two whole days of gap, split evenly.
        s = [sample(at(1, 12), 0), sample(at(3, 12), 12)]
        d = daily.build(window(12), s, now=at(3, 12.01))
        days = d["weeks"][0]["days"]
        self.assertAlmostEqual(days[1]["estimated_pct"], 3.0)   # 12h of 48h
        self.assertAlmostEqual(days[2]["estimated_pct"], 6.0)   # 24h
        self.assertAlmostEqual(days[3]["estimated_pct"], 3.0)   # 12h
        self.assertEqual(days[2]["measured_pct"], 0.0)

    def test_gaps_are_reported_with_their_amount(self):
        # Sampling begins late on day 1; the 20% already on the meter is one gap
        # from window start. A second gap of 5% follows a two-day silence.
        s = [sample(at(1, 12), 20), sample(at(1, 12.1), 20), sample(at(3, 12), 25)]
        d = daily.build(window(25), s, now=at(3, 12.05))
        gaps = d["weeks"][0]["gaps"]
        self.assertEqual(len(gaps), 2)
        self.assertEqual(gaps[0]["start"], START.isoformat())
        self.assertAlmostEqual(gaps[0]["pct"], 20.0)
        self.assertAlmostEqual(gaps[1]["pct"], 5.0)
        self.assertEqual(gaps[1]["seconds"], int(2 * 86400 - 0.1 * 3600))

    def test_silent_gaps_are_not_reported(self):
        # A long gap in which nothing accrued is not a gap worth bracketing.
        s = [sample(at(1, 12), 10), sample(at(3, 12), 10)]
        d = daily.build(window(10), s, now=at(3, 12.01))
        self.assertEqual([g["pct"] for g in d["weeks"][0]["gaps"]], [10.0])   # only the opening one

    def test_total_matches_current_reading(self):
        s = [sample(at(0, 6), 3), sample(at(2, 1), 20), sample(at(2, 1.1), 21)]
        d = daily.build(window(30), s, now=at(4, 2))
        self.assertAlmostEqual(d["weeks"][0]["total_pct"], 30.0, places=6)

    def test_reset_between_samples_starts_a_new_week(self):
        prev_reset = RESET - datetime.timedelta(days=7)
        s = [sample(prev_reset - datetime.timedelta(hours=1), 80, reset=prev_reset),
             sample(at(0, 2), 3)]
        d = daily.build(window(5), s, now=at(0, 3))
        self.assertEqual(len(d["weeks"]), 2)
        cur, prior = d["weeks"]
        self.assertTrue(cur["current"])
        self.assertFalse(prior["current"])
        self.assertAlmostEqual(cur["total_pct"], 5.0)
        self.assertAlmostEqual(prior["total_pct"], 80.0)


class Bank(unittest.TestCase):
    def test_carry_over_from_completed_days(self):
        # Day 0 used 4%, day 1 used 20%, now early on day 2 with 2% more.
        s = [sample(at(0, 0.1), 0), sample(at(0, 23.9), 4),
             sample(at(1, 0.1), 4), sample(at(1, 23.9), 24),
             sample(at(2, 0.1), 24), sample(at(2, 0.2), 26)]
        d = daily.build(window(26), s, now=at(2, 0.3))
        w = d["weeks"][0]
        a = daily.ALLOTMENT_PCT
        self.assertAlmostEqual(w["days"][0]["bank_after_pct"], round(a - 4, 2), places=1)
        self.assertAlmostEqual(w["days"][1]["bank_after_pct"], round(2 * a - 24, 2), places=1)
        self.assertAlmostEqual(w["bank_before_today_pct"], round(2 * a - 24, 2), places=1)
        today = w["today"]
        self.assertEqual(today["status"], "today")
        self.assertAlmostEqual(today["available_pct"], round(3 * a - 24, 2), places=1)
        self.assertAlmostEqual(today["remaining_pct"], round(3 * a - 26, 2), places=1)
        self.assertGreater(w["days"][1]["over_pct"], 0)     # day 1 overspent
        self.assertLess(w["days"][0]["over_pct"], 0)        # day 0 underspent


if __name__ == "__main__":
    unittest.main()
