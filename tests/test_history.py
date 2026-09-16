"""Window history: grouping real windows out of jittery reset timestamps, and
turning them into an expectation for a full window."""
import datetime as dt

from usemeup import history

BASE = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
R1 = "2026-09-08T00:00:00.123456+00:00"
R2 = "2026-09-15T00:00:00.123456+00:00"


def _s(hours, pct, resets):
    return {"ts": (BASE + dt.timedelta(hours=hours)).isoformat(),
            "used_pct": pct, "resets_at": resets}


def test_window_id_absorbs_reset_jitter_across_the_minute():
    """The reset timestamp wobbles by seconds on every reading, and the wobble
    crosses midnight, so the same week arrives as both 23:59:59 and 00:00:00.
    Grouping on the raw value turned two real weekly windows into 2,217."""
    a = history.window_id("2026-09-16T00:00:00.188574+00:00")
    b = history.window_id("2026-09-15T23:59:59.703204+00:00")
    assert a == b


def test_genuinely_different_windows_stay_apart():
    assert history.window_id(R1) != history.window_id(R2)


def test_windows_sum_only_increases():
    """A reset inside the series is a new window, never negative consumption."""
    ws = history.windows([_s(0, 0, R1), _s(24, 10, R1), _s(48, 30, R1),
                          _s(72, 5, R2), _s(96, 25, R2)])
    assert len(ws) == 2
    first, second = ws
    assert first["rise"] == 30 and first["peak"] == 30
    assert second["rise"] == 20 and second["peak"] == 25
    assert all(w["rise"] >= 0 for w in ws)


def test_typical_full_window_scales_the_daily_rate_to_the_window():
    """40 points over 4 days is 10 a day, so a 7-day window expects 70."""
    samples = [_s(h, h / 96.0 * 40.0, R1) for h in range(0, 97, 12)]
    t = history.typical_full_window(samples, 7 * 86400)
    assert t is not None
    assert abs(t["per_day"] - 10.0) < 0.01
    assert abs(t["expected_end"] - 70.0) < 0.1
    assert t["windows_seen"] == 1


def test_short_windows_get_a_proportionally_small_expectation():
    samples = [_s(h, h / 96.0 * 40.0, R1) for h in range(0, 97, 12)]
    t = history.typical_full_window(samples, 5 * 3600)
    assert abs(t["expected_end"] - 10.0 * (5 / 24.0)) < 0.01


def test_too_little_history_is_none_not_a_guess():
    """None means "no opinion". The projection has to say so rather than
    substitute a number, which is the failure this whole module replaced."""
    assert history.typical_full_window([_s(0, 0, R1), _s(6, 5, R1)], 7 * 86400) is None
    assert history.typical_full_window([], 7 * 86400) is None
    assert history.typical_full_window(None, 7 * 86400) is None


def test_a_window_never_helps_predict_itself():
    samples = ([_s(h, h / 96.0 * 40.0, R1) for h in range(0, 97, 12)]
               + [_s(h, 5.0, R2) for h in range(100, 150, 12)])
    both = history.typical_full_window(samples, 7 * 86400)
    without = history.typical_full_window(samples, 7 * 86400,
                                          exclude_id=history.window_id(R2))
    assert both["windows_seen"] == 2
    assert without["windows_seen"] == 1


def test_capped_windows_are_counted_so_the_floor_is_visible():
    """A window that hits 100 stops rising, so demand past the cap is invisible
    and every figure here is a floor. The count makes that checkable."""
    samples = [_s(h, min(100.0, h), R1) for h in range(0, 145, 12)]
    t = history.typical_full_window(samples, 7 * 86400)
    assert t["capped_windows"] == 1


def test_same_window_accepts_midnight_jitter():
    """The pair that looks like two windows and is one: the reset timestamp
    wobbles across midnight between consecutive readings."""
    assert history.same_window("2026-09-15T23:59:59.703204+00:00",
                               "2026-09-16T00:00:00.188574+00:00")


def test_same_window_rejects_genuinely_adjacent_windows():
    """The nearest real boundary is the 5-hour window. Nothing in the tolerance
    comes close to it."""
    assert not history.same_window("2026-09-16T08:30:00+00:00",
                                   "2026-09-16T13:30:00+00:00")
    assert not history.same_window(R1, R2)


def test_same_window_is_false_on_missing_values():
    assert not history.same_window(None, R1)
    assert not history.same_window(R1, "not a timestamp")
