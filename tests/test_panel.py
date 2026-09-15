"""Panel verdicts, and the one case the pace model gets wrong on its own."""
import datetime as _dt

from usemeup import panel


def _iso(offset_seconds: float) -> str:
    t = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=offset_seconds)
    return t.isoformat()


def _window(used_pct: float, elapsed_frac: float = 0.98, window_seconds: int = 7 * 86400):
    """A window `elapsed_frac` of the way through, `used_pct` spent."""
    ris = int(window_seconds * (1 - elapsed_frac))
    start, end = _iso(-(window_seconds - ris)), _iso(ris)
    return {
        "key": "weekly_all",
        "used_pct": used_pct,
        "window_seconds": window_seconds,
        "resets_in_seconds": ris,
        "resets_at": end,
        "projection": {
            "window_start": start,
            "resets_at": end,
            "now": _iso(0),
            "rate_pct_per_hour": 1.0,
            "projected_end_pct": used_pct,
            "series": [{"t": start, "pct": 0.0}, {"t": _iso(0), "pct": used_pct}],
        },
    }


def test_verdict_for_thresholds():
    assert panel.verdict_for(20)[0] == "ahead of pace"
    assert panel.verdict_for(10)[0] == "slightly ahead of pace"
    assert panel.verdict_for(0)[0] == "on pace"
    assert panel.verdict_for(-10)[0] == "a little under pace"
    assert panel.verdict_for(-20)[0] == "well under pace"


def test_spent_window_is_not_on_pace():
    """A window at 100% must never be described in pace terms. Its gap is small
    (100 used against 98 elapsed), so verdict_for alone would call it on pace."""
    assert panel.verdict_for(2.0)[0] == "on pace"
    built = panel.build_window(_window(100.0), None)
    assert built["verdict"] == "spent"
    assert built["tone"] == "critical"
    assert built["state"] == "alert"
    assert built["headline"] == "nothing left until reset"
    assert "on pace" not in built["detail"]
    assert "burning" not in built["detail"]


def test_unspent_window_still_gets_pace_language():
    built = panel.build_window(_window(84.0), None)
    assert "pace" in built["verdict"]
    assert built["state"] == "ok"


def test_severity_ranks_alert_over_fullness():
    hot = {"state": "alert", "used_pct": 40.0}
    full = {"state": "ok", "used_pct": 99.0}
    assert panel._severity(hot) > panel._severity(full)


def test_headline_states_the_outcome_not_the_slope():
    """The headline answers "where does this end up", so the landing figure
    lives there and the burn rate stays in the supporting line."""
    built = panel.build_window(_window(84.0), None)
    assert built["headline"].startswith("landing near")
    assert "at reset" in built["headline"]
    assert "burning" in built["detail"]
    assert "landing" not in built["detail"]
