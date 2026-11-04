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


# ---------------------------------------------------------------- chart axis

def test_a_landing_that_fits_keeps_a_0_to_100_axis():
    # 2026-09-29: a week forecast at 92.3% drew a 0-125 axis, an empty band
    # across the top and the chart squashed into the rest.
    for landing in (0.0, 50.0, 91.0, 92.3, 94.0):
        assert panel.axis_top(landing) == 100.0, landing


def test_no_landing_keeps_a_0_to_100_axis():
    assert panel.axis_top(None) == 100.0


def test_a_landing_near_the_cap_gets_room_for_its_label():
    assert panel.axis_top(94.1) == 125.0
    assert panel.axis_top(100.0) == 125.0


def test_an_over_cap_landing_is_drawn_not_clipped():
    assert panel.axis_top(117.0) == 125.0
    assert panel.axis_top(117.1) == 150.0
    # An exact step is not rounded past itself.
    assert panel.axis_top(142.0) == 150.0


def test_the_chart_series_uses_the_axis_rule():
    w = _window(40.0, elapsed_frac=0.5, projected_end=92.3)
    lim = {"ok": True, "windows": [w]}
    got = panel.menubar({}, lim, series=True)["windows"][0]
    assert got["y_max"] == 100.0
    over = panel.menubar({}, {"ok": True, "windows": [_window(60, 0.5, projected_end=120)]},
                         series=True)["windows"][0]
    assert over["y_max"] == 150.0


# ------------------------------------------------------------------ nudges

def test_nudge_turns_each_verdict_into_an_instruction():
    assert panel.nudge_for("well under pace", "weekly_all") == "Room to spare"
    assert panel.nudge_for("a little under pace", "weekly_all") == "Keep going"
    assert panel.nudge_for("on pace", "weekly_all") == "On pace"
    assert panel.nudge_for("slightly ahead of pace", "weekly_all") == "Ease up"
    assert panel.nudge_for("ahead of pace", "weekly_all") == "Slow down"


def test_every_pace_verdict_has_a_nudge():
    for gap in (20, 10, 0, -10, -20):
        verdict = panel.verdict_for(gap)[0]
        assert panel.nudge_for(verdict, "weekly_all"), verdict


def test_a_window_nobody_uses_gets_no_nudge():
    assert panel.nudge_for("not in use", "weekly_all") is None


def test_spent_nudge_depends_on_whether_there_is_a_way_out():
    """A spent model-scoped window can move its work to another model. A spent
    all-models or 5-hour window blocks everything, so it can only wait."""
    assert panel.nudge_for("spent", "weekly_scoped") == "Switch models"
    assert panel.nudge_for("spent", "weekly_all") == "Wait for reset"
    assert panel.nudge_for("spent", "session") == "Wait for reset"


def test_window_payload_and_menubar_carry_the_nudge():
    built = panel.build_window(_window(100.0), None)
    assert built["nudge"] == "Wait for reset"
    assert panel.build_window(_window(84.0), None)["nudge"] == panel.nudge_for(
        panel.build_window(_window(84.0), None)["verdict"], "weekly_all")
    assert "nudge" in panel.MENUBAR_FIELDS


# ---------------------------------------------------------------- daily bars
#
# Since 2026-11-04 the drop-down draws the week as seven bars above the burn-up
# chart; the web page that used to be the only place to see them is gone.

def _week(readings, now, used):
    """daily.build over a week that opened three days before `now`.

    readings: [(seconds after the week opened, pct)]."""
    from usemeup import daily
    start = now - _dt.timedelta(days=3)
    reset = start + _dt.timedelta(days=7)
    window = {"key": "weekly_all", "used_pct": used, "window_seconds": 7 * 86400,
              "resets_at": reset.isoformat()}
    samples = [{"ts": (start + _dt.timedelta(seconds=s)).isoformat(), "used_pct": p,
                "resets_at": reset.isoformat()} for s, p in readings]
    return start, daily.build(window, samples, now)


def _steady(day_totals, until_s, step=600):
    """Readings every `step` seconds, each day rising evenly to its total."""
    out, base = [], 0.0
    for d, total in enumerate(day_totals):
        for k in range(1, 86400 // step + 1):
            s = d * 86400 + k * step
            if s > until_s:
                return out
            out.append((s, round(base + (total - base) * k * step / 86400.0, 4)))
        base = total
    return out


def test_daily_view_is_the_current_week_on_the_burn_charts_clock():
    now = _dt.datetime(2026, 11, 7, 12, 0, tzinfo=_dt.timezone.utc)
    start, d = _week(_steady([20.0, 25.0, 30.0], 3 * 86400 - 600), now, 30.0)
    v = panel.daily_view(d)
    days = v["days"]
    assert len(days) == 7
    # Epoch seconds, the clock BurnChart's t0/t1 use, so the bars share its columns.
    assert days[0]["t0"] == start.timestamp()
    assert days[6]["t1"] == start.timestamp() + 7 * 86400
    assert [x["status"] for x in days] == ["past"] * 3 + ["today"] + ["future"] * 3
    assert abs(days[0]["used_pct"] - 20.0) < 0.01
    assert days[0]["estimated_pct"] == 0.0          # sampled every 10 minutes: measured
    # What today may spend is a today-only figure.
    assert "available_pct" in days[3]
    assert all("available_pct" not in x for i, x in enumerate(days) if i != 3)
    assert abs(v["share_pct"] - 100 / 7.0) < 0.01


def test_the_note_names_the_reset_hour_and_past_weeks():
    now = _dt.datetime(2026, 11, 7, 12, 0, tzinfo=_dt.timezone.utc)
    _, d = _week(_steady([10.0], 86400), now, 10.0)
    d["weeks"].append({"current": False, "total_pct": 52.4, "days": []})
    d["weeks"].append({"current": False, "total_pct": 76.0, "days": []})
    note = panel.daily_view(d)["note"]
    hour = panel._clock(panel._parse(d["weeks"][0]["resets_at"]))
    assert note.startswith("days run %s to %s, the time the week resets" % (hour, hour))
    assert note.endswith("Past weeks, newest first: 52%, 76%.")


def test_usage_in_a_sampling_gap_is_named_in_the_note():
    now = _dt.datetime(2026, 11, 7, 12, 0, tzinfo=_dt.timezone.utc)
    # Nothing sampled between hour 2 and hour 30, and 12 points accrued in it.
    readings = [(k * 600, k / 6.0) for k in range(1, 13)]                # 2.0 by hour 2
    readings += [(30 * 3600 + k * 600, 14.0 + k / 6.0) for k in range(0, 7)]  # 15.0 by 31
    _, d = _week(readings, now, 15.0)
    v = panel.daily_view(d)
    assert "12.0% of this week came while nothing was sampling" in v["note"]
    assert v["days"][0]["estimated_pct"] > 0 and v["days"][1]["estimated_pct"] > 0


def test_today_line_states_the_share_and_what_is_left():
    share = 100 / 7.0
    plain = {"consumed_pct": 11.0, "available_pct": share, "remaining_pct": share - 11.0}
    assert panel._today_line(share, plain, 0.0) == "today: 11.0% of a 14.3% share, 3.3% left"
    banked = {"consumed_pct": 5.0, "available_pct": share + 3.57,
              "remaining_pct": share + 3.57 - 5.0}
    assert panel._today_line(share, banked, 3.57) == (
        "today: 5.0% of 17.9% (a 14.3% share plus 3.6% unspent on earlier days), 12.9% left")
    behind = {"consumed_pct": 12.0, "available_pct": share - 5.0,
              "remaining_pct": share - 5.0 - 12.0}
    assert panel._today_line(share, behind, -5.0) == (
        "today: 12.0% of 9.3% (a 14.3% share less 5.0% overspent on earlier days), 2.7% over")
    broke = {"consumed_pct": 3.0, "available_pct": share - 16.0,
             "remaining_pct": share - 16.0 - 3.0}
    assert panel._today_line(share, broke, -16.0) == (
        "today: 3.0% used, with nothing left for it, since earlier days went 16.0% "
        "over their shares")
    assert panel._today_line(share, None, 0.0) is None


def test_reset_hour_rounds_to_the_minute():
    t = _dt.datetime(2026, 11, 10, 19, 59, 59, 600000).timestamp()     # local clock
    assert panel._clock(t) == "8 PM"
    assert panel._clock(_dt.datetime(2026, 11, 10, 19, 30).timestamp()) == "7:30 PM"
    assert panel._clock(_dt.datetime(2026, 11, 10, 0, 0).timestamp()) == "12 AM"


def test_daily_view_passes_errors_through_and_ignores_no_data():
    assert panel.daily_view({"error": "ValueError: x"}) == {"error": "ValueError: x"}
    assert panel.daily_view(None) is None
    assert panel.daily_view({"allotment_pct": 14.29, "weeks": []}) is None


def test_menubar_gives_weekly_windows_their_bars_only_with_series():
    from usemeup import daily
    w = _window(60, 0.5, projected_end=120)
    w["daily"] = daily.build(w, [])
    session = dict(_window(30, 0.5, window_seconds=5 * 3600), key="session")
    limits = {"ok": True, "windows": [w, session]}
    full = {x["key"]: x for x in panel.menubar({}, limits, series=True)["windows"]}
    lean = {x["key"]: x for x in panel.menubar({}, limits)["windows"]}
    assert len(full["weekly_all"]["daily"]["days"]) == 7
    assert "daily" not in full["session"]          # a 5-hour window has no days
    assert "daily" not in lean["weekly_all"]        # the minute poll stays small
