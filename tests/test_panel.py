"""Panel verdicts and colour states.

Two of these lock bugs the menu bar surfaced on its first run with real data:
a spent window being described in pace terms, and amber being unreachable.
"""
import datetime as _dt

from usemeup import panel


def _iso(offset_seconds: float) -> str:
    t = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=offset_seconds)
    return t.isoformat()


def _window(used_pct, elapsed_frac=0.98, window_seconds=7 * 86400, projected_end=None):
    """A window `elapsed_frac` of the way through, `used_pct` spent.

    `projected_end` defaults to `used_pct`, i.e. a window that stops right where
    it is. Pass a number over 100 for one heading through the cap.
    """
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
            "projected_end_pct": used_pct if projected_end is None else projected_end,
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


def test_headline_states_the_outcome_not_the_slope():
    """The headline answers "where does this end up", so the landing figure
    lives there and the burn rate stays in the supporting line."""
    built = panel.build_window(_window(84.0), None)
    assert built["headline"].startswith("landing near")
    assert "at reset" in built["headline"]
    assert "burning" in built["detail"]
    assert "landing" not in built["detail"]


# ------------------------------------------------------------------ colour

def test_ahead_of_pace_is_amber_not_red():
    """Red belongs to the limit. Ahead of pace warns about where a window is
    going, so it is amber, and amber has to be reachable at all: the two-state
    version fired alert at the same gap > 5 that starts the "slightly ahead"
    verdict, which collapsed amber out of existence."""
    built = panel.build_window(_window(60.0, elapsed_frac=0.40), None)
    assert built["verdict"] == "ahead of pace"
    assert built["state"] == "watch"


def test_on_pace_and_under_pace_stay_green():
    assert panel.build_window(_window(40.0, elapsed_frac=0.40), None)["state"] == "ok"
    assert panel.build_window(_window(20.0, elapsed_frac=0.60), None)["state"] == "ok"


def test_projected_to_cross_is_red_even_when_far_from_the_cap():
    """40% used is nowhere near the wall, but a projection through 100 before
    reset means it gets there. That distinction is why the pace model exists."""
    built = panel.build_window(_window(40.0, elapsed_frac=0.30, projected_end=140.0), None)
    assert built["state"] == "alert"


def test_near_the_limit_is_red_whatever_the_pace():
    """96% used with the window nearly over is under pace and still red: the
    headroom left is too small for pace to be the question."""
    built = panel.build_window(_window(96.0, elapsed_frac=0.99), None)
    assert built["state"] == "alert"


def test_almost_full_but_coasting_to_reset_is_not_red():
    """The counterpart: 92% with 2% of the window left lands near 92% and never
    touches the wall. Proximity alone would false-alarm here."""
    built = panel.build_window(_window(92.0, elapsed_frac=0.98), None)
    assert built["state"] == "ok"


def test_severity_ranks_alert_over_watch_over_ok():
    a = {"state": "alert", "used_pct": 40.0}
    w = {"state": "watch", "used_pct": 50.0}
    o = {"state": "ok", "used_pct": 99.0}
    assert panel._severity(a) > panel._severity(w) > panel._severity(o)


# ------------------------------------------------------------------ projection

def test_blend_prefers_history_when_the_window_is_too_young():
    slope, basis = panel.blend_slope(observed=None, prior_end=104.0, span=0.001)
    assert (slope, basis) == (104.0, "history")


def test_blend_prefers_the_window_once_it_has_grown_up():
    slope, basis = panel.blend_slope(observed=90.0, prior_end=104.0,
                                     span=panel.SLOPE_FULL_SPAN)
    assert (slope, basis) == (90.0, "observed")


def test_blend_is_proportional_in_between():
    mid = (panel.SLOPE_MIN_SPAN + panel.SLOPE_FULL_SPAN) / 2
    slope, basis = panel.blend_slope(observed=100.0, prior_end=200.0, span=mid)
    assert basis == "blend"
    assert abs(slope - 150.0) < 1e-9


def test_blend_never_invents_a_number_from_nothing():
    """The bug this replaced returned a slope of zero when evidence was thin,
    which projected a window at 21% on day one to finish the week at 21%."""
    assert panel.blend_slope(None, None, 0.0) == (None, "none")


def test_no_basis_says_so_in_the_headline():
    flat = {"cost": [1.0] * 24, "dow_cost": [[1.0] * 24 for _ in range(7)]}
    weights = panel.duty_weights(flat)
    built = panel.build_window(_window(21.0, elapsed_frac=0.002), weights, None)
    assert built["headline"] == "not enough history to project yet"
    assert built["source"] == "none"


def test_a_thin_window_with_history_projects_past_the_cap():
    flat = {"cost": [1.0] * 24, "dow_cost": [[1.0] * 24 for _ in range(7)]}
    weights = panel.duty_weights(flat)
    prior = {"expected_end": 104.0, "days_observed": 9.0}
    built = panel.build_window(_window(21.0, elapsed_frac=0.002), weights, prior)
    assert built["source"] == "history"
    assert built["over_cap"] is True
    assert built["headline"].startswith("runs out around")
    assert "9 days of history" in built["detail"]


# ---------------------------------------------------------------- alerts

def _built(used, elapsed_frac, projected_end=None, **over):
    w = panel.build_window(_window(used, elapsed_frac, projected_end=projected_end), None)
    w.update(over)
    return w


def test_a_calm_window_raises_nothing():
    assert panel.alert_for(_built(40, 0.5)) is None


def test_amber_is_not_worth_a_notification():
    w = _built(70, 0.5)
    assert w["state"] == "watch"
    assert panel.alert_for(w) is None


def test_forecast_to_cross_raises_a_forecast_alert():
    a = panel.alert_for(_built(60, 0.5, projected_end=120))
    assert a["kind"] == "forecast"
    assert a["title"].startswith("All models: ")
    assert "60% used" in a["body"]


def test_a_spent_window_raises_spent_not_forecast():
    a = panel.alert_for(_built(100, 0.9))
    assert a["kind"] == "spent"
    assert a["title"] == "All models is spent"


def test_the_id_is_stable_so_a_wobbling_forecast_fires_once():
    # The same window seen twice, a few minutes apart: usage moved and the
    # forecast moved. (Reset-time jitter is window_id's job; see test_history.)
    first = _built(60, 0.5, projected_end=120)
    later = dict(first, used_pct=64.0, headline="landing near 101% at reset")
    one, two = panel.alert_for(first), panel.alert_for(later)
    assert one["title"] != two["title"]
    assert one["id"] == two["id"]


def test_spent_and_forecast_are_separate_events_in_one_window():
    f = panel.alert_for(_built(60, 0.5, projected_end=120))
    s = panel.alert_for(_built(100, 0.5))
    assert f["id"] != s["id"]
    assert f["id"].rsplit("|", 1)[0] == s["id"].rsplit("|", 1)[0]


def test_a_forecast_drawn_only_from_history_is_withheld():
    # Otherwise an account that usually runs out is told so at every reset.
    assert panel.alert_for(_built(30, 0.5, projected_end=120, source="history")) is None


def test_a_forecast_from_a_window_minutes_old_is_withheld():
    assert panel.alert_for(_built(6, 0.03, projected_end=200)) is None


def test_near_the_limit_is_a_measurement_and_is_never_withheld():
    a = panel.alert_for(_built(96, 0.03, source="history"))
    assert a is not None and a["kind"] == "forecast"


def test_menubar_payload_carries_alerts():
    limits = {"ok": True, "windows": [_window(60, 0.5, projected_end=120)]}
    out = panel.menubar({}, limits)
    assert [a["kind"] for a in out["alerts"]] == ["forecast"]
    assert panel.menubar({}, {"ok": False})["alerts"] == []


def test_menubar_series_only_on_request():
    # The every-minute bar poll stays small; the drop-down asks for the charts.
    limits = {"ok": True, "windows": [_window(60, 0.5, projected_end=120)]}
    lean = panel.menubar({}, limits)["windows"][0]
    full = panel.menubar({}, limits, series=True)["windows"][0]
    for k in ("reference", "observed", "projection", "t0", "t1", "now", "y_max"):
        assert k not in lean, k
        assert k in full, k
    assert len(full["observed"]) >= 2 and len(full["observed"][0]) == 2


# ---------------------------------------------------------------- advice

def test_green_windows_get_no_advice():
    assert panel.advice_for("weekly_all", 40, "ok", 1.0, 30, 10, "Tue") is None


def test_the_budget_lands_exactly_at_100():
    # 60 left over 30 working hours is 2%/h, 20% a working day, against 30 now.
    a = panel.advice_for("weekly_all", 40, "alert", 3.0, 30, 10, "Tue")
    assert a == "To last until reset, keep it under 20% a working day (the current pace is 30%)."


def test_small_budgets_keep_a_decimal():
    a = panel.advice_for("weekly_all", 90, "alert", 1.0, 20, 10, "Tue")
    assert "under 5.0% a working day" in a


def test_clock_windows_budget_by_the_hour():
    a = panel.advice_for("session", 70, "alert", 20.0, 2, None, None)
    assert "under 15% an hour" in a and "current pace is 20%" in a


def test_only_the_scoped_window_offers_another_model():
    assert "another model" in panel.advice_for("weekly_scoped", 70, "alert", 3.0, 20, 10, "Tue")
    assert "another model" not in panel.advice_for("weekly_all", 70, "alert", 3.0, 20, 10, "Tue")


def test_a_spent_scoped_window_says_where_to_go_and_what_it_costs():
    a = panel.advice_for("weekly_scoped", 100, "alert", None, 20, 10, "Tuesday 8:00 PM")
    assert a.startswith("Move work to another model until it resets Tuesday 8:00 PM")
    assert "all models" in a
    assert panel.advice_for("weekly_all", 100, "alert", None, 20, 10, "Tue") is None


def test_already_inside_the_budget_is_not_advised():
    # Amber on the verdict, but the arithmetic already lands under 100.
    assert panel.advice_for("weekly_all", 50, "watch", 0.5, 60, 10, "Tue") is None


def test_the_last_half_hour_gets_no_budget():
    assert panel.advice_for("session", 90, "alert", 30.0, 0.3, None, None) is None


def test_built_windows_carry_advice_and_the_menubar_passes_it_on():
    w = panel.build_window(_window(60, 0.5, projected_end=120), None)
    assert w["advice"] and "an hour" in w["advice"]
    out = panel.menubar({}, {"ok": True, "windows": [_window(60, 0.5, projected_end=120)]})
    assert out["windows"][0]["advice"] == w["advice"]
    assert w["advice"] in out["alerts"][0]["body"]


# ---------------------------------------------------------------- idle windows

def _flat_weights():
    return panel.duty_weights({"cost": [1.0] * 24, "dow_cost": [[1.0] * 24 for _ in range(7)]})


def test_an_unused_window_stays_at_zero_whatever_history_says():
    """The Fable bug, 2026-09-23: 0% used a day into the week, and history from
    weeks when Fable ran out projected it to land near 78%."""
    prior = {"expected_end": 105.0, "days_observed": 16.0,
             "last_rise": 100.0, "last_coverage": 1.0}
    built = panel.build_window(_window(0.0, elapsed_frac=0.09), _flat_weights(), prior)
    assert built["source"] == "idle"
    assert built["headline"] == "landing near 0% at reset"
    assert built["verdict"] == "not in use"
    assert built["state"] == "ok"
    assert built["over_cap"] is False
    assert "burning" not in built["detail"]
    assert "under pace" not in built["verdict"]


def test_a_fresh_window_still_leans_on_history():
    """The 09-16 fix must survive: minutes after a reset a zero is an early
    start, not evidence, so history still carries the projection."""
    prior = {"expected_end": 104.0, "days_observed": 9.0,
             "last_rise": 85.0, "last_coverage": 1.0}
    built = panel.build_window(_window(0.0, elapsed_frac=0.002), _flat_weights(), prior)
    assert built["source"] == "history"
    assert built["verdict"] != "not in use"


def test_a_window_after_an_unused_week_opens_at_zero():
    """Reset night: no working time has passed, but the whole last window went
    unused, so the new one is not forecast to run out before anyone is awake."""
    prior = {"expected_end": 72.0, "days_observed": 22.0,
             "last_rise": 0.0, "last_coverage": 1.0}
    built = panel.build_window(_window(0.0, elapsed_frac=0.001), _flat_weights(), prior)
    assert built["source"] == "idle"
    assert built["headline"] == "landing near 0% at reset"


def test_a_barely_sampled_quiet_week_is_not_evidence():
    prior = {"expected_end": 72.0, "days_observed": 22.0,
             "last_rise": 0.0, "last_coverage": 0.1}
    assert panel.idle_window(0.0, 0.0, prior) is False


def test_any_use_ends_idle():
    """Idle never hides consumption: once anything is spent, the blend is back."""
    prior = {"expected_end": 105.0, "days_observed": 16.0,
             "last_rise": 0.0, "last_coverage": 1.0}
    assert panel.idle_window(1.0, 0.5, prior) is False
    built = panel.build_window(_window(2.0, elapsed_frac=0.09), _flat_weights(), prior)
    assert built["source"] != "idle"


def test_no_history_is_not_idle():
    assert panel.idle_window(0.0, 0.5, None) is False
    assert panel.idle_window(0.0, 0.5, {"expected_end": None}) is False
