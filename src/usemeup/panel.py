"""Panel payload: the two weekly burn-up charts, already re-based.

The dashboard's pace model lived in index.html as JavaScript, which is fine
while the dashboard is the only reader. It stopped being fine the moment a
second surface (the Transom notch panel) wanted the same two charts: a model
that exists twice drifts, and this one is still being tuned.

So the model lives here now, in Python, and `/api/panel` serves it already
computed. A client draws three polylines and prints two strings. Nothing about
the pace basis, the weekday weighting or the projection is a client's business.

This is a PORT of `dutyWeights` / `dutyCurve` / `paceBasis` / `paceLine` from
index.html, kept deliberately line-for-line comparable so the two can be diffed
by eye until the page is switched over to read from here too.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional, Sequence, Tuple

# The working day, in hours. The dashboard lets this be picked in the UI; the
# panel has no UI, so it takes the value Wekesa settled on.
HOURS_PER_DAY = 10

DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DOW_BLEND = 0.6      # weight on the day's own hour shape, rest from all days
DOW_SHRINK = 0.25    # pull weekday intensities a quarter of the way to flat

# Windows shorter than this stay on the wall clock. Inside a 5-hour window the
# duty model runs out of working time before the window closes and reports
# 100% elapsed with an hour still on the clock, which is true and useless.
MIN_WORKDAY_WINDOW = 36 * 3600

# Two weekly windows, in the order the panel stacks them.
PANEL_KEYS = ("weekly_all", "weekly_scoped")
PANEL_LABELS = {"weekly_all": "All models", "weekly_scoped": "Fable"}

# How many points each polyline is reduced to. A 400pt-wide chart cannot show
# more, and the payload crosses a socket on every panel open.
MAX_POINTS = 64


# ---------------------------------------------------------------- helpers

def _parse(ts: Optional[str]) -> Optional[float]:
    """ISO 8601 (with Z or offset) to epoch seconds."""
    if not ts:
        return None
    try:
        s = ts.replace("Z", "+00:00")
        d = _dt.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return d.timestamp()
    except Exception:
        return None


def _norm(a: Sequence[float]) -> Optional[List[float]]:
    t = float(sum(a))
    return [v / t for v in a] if t > 0 else None


def _cap_top(a: Sequence[float], want: int) -> Tuple[Optional[List[float]], int]:
    """Keep the busiest `want` hours, zero the rest, renormalise."""
    ranked = sorted(((v, i) for i, v in enumerate(a)), key=lambda p: -p[0])
    keep = {i for v, i in ranked[:want] if v > 0}
    out = [max(v, 1e-9) if i in keep else 0.0 for i, v in enumerate(a)]
    return _norm(out), len(keep)


def _hr12(h: int) -> str:
    return "%d %s" % (12 if h % 12 == 0 else h % 12, "AM" if (h < 12 or h == 24) else "PM")


def _hour_ranges(hours: Sequence[int]) -> str:
    """'9 AM-8 PM', or '8 PM-2 AM, 9 AM-1 PM' when the working day is split."""
    hs = list(hours)
    if not hs:
        return ""
    runs: List[List[int]] = []
    start = prev = hs[0]
    for h in hs[1:]:
        if h == prev + 1:
            prev = h
            continue
        runs.append([start, prev])
        start = prev = h
    runs.append([start, prev])
    if len(runs) > 1 and runs[0][0] == 0 and runs[-1][1] == 23:
        first = runs.pop(0)          # a run across midnight reads as one
        runs[-1][1] = first[1] + 24
    return ", ".join("%s–%s" % (_hr12(a % 24), _hr12((b + 1) % 24)) for a, b in runs)


def _thin(pts: Sequence[Sequence[float]], cap: int = MAX_POINTS) -> List[List[float]]:
    """Even decimation that always keeps the first and last point."""
    n = len(pts)
    if n <= cap:
        return [[round(p[0], 3), round(p[1], 4)] for p in pts]
    step = (n - 1) / float(cap - 1)
    idx = sorted({int(round(i * step)) for i in range(cap)} | {0, n - 1})
    return [[round(pts[i][0], 3), round(pts[i][1], 4)] for i in idx]


# ---------------------------------------------------------------- duty model

def duty_weights(by_hour: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Per-hour weights on a 7x24 grid (Monday first), mean 1.0 per hour.

    Two things are learned, not assumed: the shape of each weekday (its busiest
    N hours carry that day's budget) and the intensity of each weekday relative
    to the others. Saturday really is lighter than Wednesday, and Sunday really
    is not a day off.
    """
    if not by_hour:
        return None
    api = by_hour.get("api") if isinstance(by_hour.get("api"), dict) else None
    if api and api.get("error"):
        api = None
    # Hours prefer the rate-limit samples: they are account-wide, so they see
    # the cloud sessions the transcript index is blind to. Transcripts stay the
    # source for the weekday split until the sampler has enough weeks for it.
    api_ok = bool(
        api
        and len(api.get("hour") or []) == 24
        and any(v > 0 for v in api["hour"])
        and (api.get("movement_pct") or 0) >= 20
        and (api.get("moving_intervals") or 0) >= 30
        and (api.get("days") or 0) >= 3
    )
    if api_ok:
        flat = list(api["hour"])
    else:
        cost = by_hour.get("cost") or []
        flat = list(cost) if any(v > 0 for v in cost) else list(by_hour.get("calls") or [])
    if len(flat) != 24 or not any(v > 0 for v in flat):
        return None

    grid = by_hour.get("dow_cost")
    grid = grid if (isinstance(grid, list) and len(grid) == 7) else None
    want = max(1, min(24, int(HOURS_PER_DAY)))

    flat_shape = _norm(flat)
    if not flat_shape:
        return None

    share = [1 / 7.0] * 7
    if grid:
        tot = [float(sum(r)) for r in grid]
        s = sum(tot)
        if s > 0:
            share = [(1 - DOW_SHRINK) * (v / s) + DOW_SHRINK / 7 for v in tot]

    w: List[List[float]] = []
    per_day_hours: List[int] = []
    for d in range(7):
        own = _norm(list(grid[d])) if grid else None
        mixed = ([DOW_BLEND * own[h] + (1 - DOW_BLEND) * flat_shape[h] for h in range(24)]
                 if own else list(flat_shape))
        shape, hours = _cap_top(mixed, want)
        if not shape:
            return None
        per_day_hours.append(hours)
        w.append([v * share[d] * 7 * 24 for v in shape])   # grid mean is 1.0/hour

    order = sorted(range(7), key=lambda i: -share[i])
    lab_shape, _ = _cap_top(flat_shape, want)
    label = _hour_ranges(sorted(i for i, v in enumerate(lab_shape or []) if v > 0))
    return {
        "w": w,
        "days": by_hour.get("days") or 0,
        "api_hours": api_ok,
        "api": api if api_ok else None,
        "hours_per_day": int(round(sum(per_day_hours) / 7.0)),
        "by_day": bool(grid),
        "heaviest": DOW[order[0]],
        "lightest": DOW[order[6]],
        "label": label,
    }


def duty_curve(t0: float, t1: float, w: List[List[float]]) -> Tuple[List[List[float]], float]:
    """Cumulative share of the window's working time, at 15-minute resolution."""
    n = max(24, min(2000, int(round((t1 - t0) / 900.0))))
    dt = (t1 - t0) / n
    pts: List[List[float]] = [[t0, 0.0]]
    acc = 0.0
    active = 0.0
    for i in range(n):
        a = t0 + dt * i
        mid = _dt.datetime.fromtimestamp(a + dt / 2)      # local time, as the JS does
        v = w[mid.weekday()][mid.hour]                    # Python weekday() is already Monday=0
        acc += v * dt
        if v > 0:
            active += dt
        pts.append([a + dt, acc])
    total = acc or 1.0
    for q in pts:
        q[1] /= total
    return pts, active / 3600.0


def duty_at(pts: List[List[float]], t: float) -> float:
    if t <= pts[0][0]:
        return 0.0
    if t >= pts[-1][0]:
        return 1.0
    lo, hi = 0, len(pts) - 1
    while hi - lo > 1:
        m = (lo + hi) // 2
        if pts[m][0] <= t:
            lo = m
        else:
            hi = m
    ta, va = pts[lo]
    tb, vb = pts[hi]
    return va + (vb - va) * (t - ta) / max(1e-9, tb - ta)


def duty_inv(pts: List[List[float]], v: float) -> Optional[float]:
    """First moment the curve reaches v, or None if it never does in-window."""
    if v <= 0:
        return pts[0][0]
    for i in range(1, len(pts)):
        if pts[i][1] >= v:
            ta, va = pts[i - 1]
            tb, vb = pts[i]
            return ta + (tb - ta) * (v - va) / max(1e-9, vb - va)
    return None


# ---------------------------------------------------------------- pace basis

# How much of a window's working time must be on the clock before its own
# slope means anything, and how much before it can stand alone. Between the two
# the window's evidence and the account's history are blended in proportion.
SLOPE_MIN_SPAN = 0.05    # ~3.5 working hours of a 70-hour week
SLOPE_FULL_SPAN = 0.25   # ~1.75 working days


def blend_slope(observed: Optional[float], prior_end: Optional[float],
                span: float) -> Tuple[Optional[float], str]:
    """Pick a slope from the window's own evidence, the account's history, or both.

    `observed` is points per unit duty measured inside this window, or None when
    there is too little of the window to measure. `prior_end` is what a full
    window of this kind usually consumes. `span` is how much duty the
    observation covers, which is the only thing that decides how far to trust it.

    Returns (slope, basis) where basis is one of observed, blend, history, none.
    `none` means say so; it must never be turned into a number downstream.
    """
    if observed is not None and prior_end is not None:
        conf = (span - SLOPE_MIN_SPAN) / (SLOPE_FULL_SPAN - SLOPE_MIN_SPAN)
        conf = min(1.0, max(0.0, conf))
        return conf * observed + (1.0 - conf) * prior_end, ("observed" if conf >= 1.0 else "blend")
    if observed is not None:
        return observed, "observed"
    if prior_end is not None:
        return prior_end, "history"
    return None, "none"


def pace_basis(win: Dict[str, Any], weights: Optional[Dict[str, Any]],
               prior: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """{'on': False} means the caller should fall back to the wall clock.

    `prior` is history.typical_full_window() for this key: what a complete
    window usually consumes, in the same units as slope. It carries the
    projection while the window is too young to speak for itself."""
    off = {"on": False}
    if not weights:
        return off
    if not win.get("window_seconds") or win["window_seconds"] < MIN_WORKDAY_WINDOW:
        return off
    p = win.get("projection") or {}
    if p.get("error") or not p.get("window_start") or not p.get("resets_at") or not p.get("now"):
        return off
    t0, t1, now = _parse(p["window_start"]), _parse(p["resets_at"]), _parse(p["now"])
    if t0 is None or t1 is None or now is None or not (t1 > t0):
        return off

    pts, active_hours = duty_curve(t0, t1, weights["w"])
    d_now = duty_at(pts, now)
    used = float(win.get("used_pct") or 0)

    # Slope measured in working time, not wall time, so an overnight gap in the
    # samples is not read as a slowdown.
    ser = []
    for s in (p.get("series") or []):
        t = _parse(s.get("t"))
        pct = s.get("pct")
        if t is not None and pct is not None and t0 <= t <= now:
            ser.append((t, float(pct)))

    observed, span = None, 0.0
    if len(ser) >= 2:
        a, z = ser[0], ser[-1]
        span = duty_at(pts, z[0]) - duty_at(pts, a[0])
        if span > SLOPE_MIN_SPAN and z[1] >= a[1]:
            observed = (z[1] - a[1]) / span

    # An earlier version divided by whatever span it had and, when that was too
    # thin to trust, fell back to a slope of zero. Zero is the worst answer in
    # the set: it tells someone who has been working all morning that they will
    # finish exactly where they stand, which is how a window at 21% on day one
    # claimed it would land at 21%. Thin evidence now defers to history, and
    # when there is no history either the projection says so instead of
    # inventing a number.
    slope, basis = blend_slope(observed, (prior or {}).get("expected_end"), span)

    if slope is None:
        end = exhausted = None
    else:
        end = max(0.0, used + slope * (1 - d_now))
        exhausted = (duty_inv(pts, d_now + (100 - used) / slope)
                     if (slope > 0 and end > 100) else None)
    return {
        "on": True, "curve": pts, "d_now": d_now, "slope": slope, "end": end,
        "exhausted": exhausted, "basis": basis, "active_hours": active_hours,
        "observed": observed, "span": span, "prior": prior,
        "rate_per_active_hour": ((slope / active_hours)
                                 if (slope is not None and active_hours > 0) else None),
        "t0": t0, "t1": t1, "now": now, "used": used,
    }


# Where the verdict starts calling a window ahead of itself. Shared with
# verdict_for so the colour and the words change on the same point.
AHEAD_GAP = 5.0

# Headroom small enough that pace stops being the question. At 95% used there
# is nothing left to pace.
NEAR_LIMIT_PCT = 95.0


def chart_state(pct: float, gap: float, will_exceed: bool = False) -> str:
    """Three states: ok, watch, alert.

    Red belongs to the limit, not to the slope. A window is red when it is
    spent, when the headroom left is negligible, or when the model says it
    crosses 100 before it resets. Running ahead of pace is amber: a warning
    about where this is going, not a report that it arrived.

    An earlier version had two states and made alert fire at gap > 5, the same
    point verdict_for starts saying "slightly ahead". That collapsed amber out
    of existence: every window ahead of pace went straight to red, so red
    stopped meaning anything.

    Proximity alone would be just as wrong in the other direction. At 92% used
    with 2% of the window left you land near 94% and never touch the wall; at
    92% with 40% left you are going to hit it. `will_exceed` separates those,
    and telling them apart is the whole reason the pace model exists.
    """
    if pct >= NEAR_LIMIT_PCT or will_exceed:
        return "alert"
    if gap > AHEAD_GAP:
        return "watch"
    return "ok"


def verdict_for(gap: float) -> Tuple[str, str, str]:
    """(verdict, consequence, tone). The consequence belongs under the headline,
    not inside it."""
    if gap > 15:
        return "ahead of pace", "on track to run out early", "critical"
    if gap > 5:
        return "slightly ahead of pace", "", "warning"
    if gap < -15:
        return "well under pace", "most of this window will expire unused", "good"
    if gap < -5:
        return "a little under pace", "", "good"
    return "on pace", "", "neutral"


# ---------------------------------------------------------------- payload

def _fmt_reset(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    d = _dt.datetime.fromtimestamp(epoch)
    hour = d.strftime("%-I:%M %p") if hasattr(d, "strftime") else ""
    return "%s %s" % (d.strftime("%A"), hour)


def _pct(v: float) -> str:
    """A budget figure, precise where it is small enough for a decimal to matter."""
    return ("%.0f%%" % v) if v >= 10 else ("%.1f%%" % v)


def advice_for(key: str, used: float, state: str, rate: Optional[float],
               hours_left: Optional[float], per_day: Optional[float],
               reset: Optional[str]) -> Optional[str]:
    """One sentence on what change lands this window at 100% by reset, or None.

    The budget is the headroom spread over the time that will actually be
    spent working before the reset: working hours on the work-day basis, wall
    hours on the clock. It is set beside the current rate on the same basis,
    because "keep it under 4% a working day" means nothing until you know you
    are doing 11.

    Only for windows that are not green. Advice nobody needs is noise, and it
    trains people to skip the line that matters on the day it does.

    A model-scoped window has a way out the all-models window does not: the
    same work on another model. It is offered, with its cost stated, because
    moving work off a scoped model still spends the all-models window.
    """
    scoped = key.startswith("weekly_scoped")
    if used >= 100:
        if scoped:
            return "Move work to another model until it resets%s. It still counts toward all models." % (
                (" " + reset) if reset else "")
        return None
    if state == "ok" or not hours_left or hours_left <= 0.5:
        return None
    budget_h = (100.0 - used) / hours_left
    if per_day:
        budget, unit = budget_h * per_day, "a working day"
        now = rate * per_day if rate else None
    else:
        budget, unit = budget_h, "an hour"
        now = rate
    if now is not None and now <= budget:
        return None      # already inside the budget; the verdict says ahead of pace, the maths says fine
    text = "To last until reset, keep it under %s %s" % (_pct(budget), unit)
    if now:
        text += " (the current pace is %s)" % _pct(now)
    text += "."
    if scoped:
        text += " Or move the rest to another model."
    return text


def build_window(win: Dict[str, Any], weights: Optional[Dict[str, Any]],
                 prior: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    p = win.get("projection") or {}
    series = p.get("series") or []
    if p.get("error") or len(series) < 2:
        return None
    b = pace_basis(win, weights, prior)
    used = float(win.get("used_pct") or 0)
    ws, ris = win.get("window_seconds"), win.get("resets_in_seconds")
    if not ws or ris is None:
        return None

    clock_pct = (ws - ris) / ws * 100.0
    elapsed_pct = (b["d_now"] * 100.0) if b["on"] else clock_pct
    gap = used - elapsed_pct
    if used >= 100:
        # Pace is a claim about the future and a spent window has none. At 100%
        # used with 98% elapsed the gap is ~2, so verdict_for says "on pace",
        # which is arithmetically true and reads as reassurance to someone who
        # is, in fact, out. chart_state already carves out 100; so does this.
        verdict, consequence, tone = "spent", "", "critical"
    else:
        verdict, consequence, tone = verdict_for(gap)

    t0 = _parse(p.get("window_start"))
    t1 = _parse(p.get("resets_at"))
    now = _parse(p.get("now"))
    if t0 is None or t1 is None or now is None:
        return None

    proj_end = b["end"] if b["on"] else float(p.get("projected_end_pct") or used)
    # None means there is no basis to project from yet. The axis falls back to
    # where the window already stands while the headline says so out loud.
    pe = used if proj_end is None else proj_end
    # Headroom above 100 so an over-cap projection is visible rather than clipped.
    y_max = max(100.0, (int((pe + 8) / 25) + 1) * 25.0) if pe + 8 > 100 else 100.0

    observed = [[_parse(s["t"]), float(s["pct"])] for s in series if _parse(s.get("t")) is not None]

    if b["on"]:
        reference = [[t, v * 100.0] for t, v in b["curve"]]
        if b["slope"] is None:
            projection, exhausted = [], None
        else:
            fwd = [[t, used + b["slope"] * (v - b["d_now"])] for t, v in b["curve"] if t >= now]
            fwd.insert(0, [now, used])
            projection = fwd
            exhausted = b["exhausted"]
    else:
        reference = [[t0, 0.0], [t1, 100.0]]
        projection = [[now, used], [t1, proj_end]]
        exhausted = _parse(p.get("exhausted_at"))

    elapsed_days = (ws - ris) / 86400.0
    total_days = ws / 86400.0
    kicker = "%s%.0f%% of %s elapsed" % (
        ("day %d of %d · " % (int(elapsed_days) + 1, round(total_days))) if total_days >= 2 else "",
        elapsed_pct,
        "your working time" if b["on"] else "the window",
    )

    # The headline is where this window ENDS UP, because that is the question
    # being asked: will I get cut off before I am done. "a little under pace"
    # describes the slope and makes the reader do the last step themselves.
    # The slope stays, demoted to supporting evidence under the outcome.
    if used >= 100:
        headline = "nothing left until reset"
    elif proj_end is None:
        headline = "not enough history to project yet"
    elif exhausted:
        headline = "runs out around %s" % _fmt_reset(exhausted)
    elif b["on"]:
        headline = "landing near %.0f%% at reset" % b["end"]
    else:
        headline = "landing near %.0f%% at reset" % float(p.get("projected_end_pct") or used)

    bits: List[str] = []
    # "on track to run out early" under a headline reading "runs out around
    # Monday 11:38 AM" is the same sentence twice.
    if consequence and not headline.startswith("runs out"):
        bits.append(consequence)
    if used >= 100:
        # No burn rate: it describes a future this window does not have.
        pass
    elif b["on"]:
        if b["rate_per_active_hour"] is not None:
            bits.append("burning %.2f%% per working hour" % b["rate_per_active_hour"])
        # Name the source. A figure drawn mostly from past weeks must not read
        # as a measurement of this one.
        days = (b.get("prior") or {}).get("days_observed")
        if days and b["basis"] == "history":
            bits.append("projected from %.0f days of history" % days)
        elif days and b["basis"] == "blend":
            bits.append("this window blended with %.0f days of history" % days)
    else:
        bits.append("burning %.2f%% per hour" % float(p.get("rate_pct_per_hour") or 0.0))

    state = chart_state(used, gap, bool(
        (proj_end is not None and proj_end > 100) or exhausted is not None))
    if b["on"]:
        hours_left = (b.get("active_hours") or 0) * max(0.0, 1.0 - b["d_now"])
        per_day, rate = (weights or {}).get("hours_per_day"), b.get("rate_per_active_hour")
    else:
        hours_left = ris / 3600.0
        per_day, rate = None, float(p.get("rate_pct_per_hour") or 0.0) or None
    advice = advice_for(win.get("key") or "", used, state, rate, hours_left, per_day,
                        _fmt_reset(t1))

    return {
        "key": win.get("key"),
        "headline": headline,
        "advice": advice,
        "label": PANEL_LABELS.get(win.get("key"), win.get("name") or win.get("key")),
        "used_pct": used,
        "verdict": verdict,
        "tone": tone,
        "state": state,
        "source": b.get("basis") if b["on"] else "clock",
        "kicker": kicker,
        "detail": " · ".join(bits),
        "basis": "workday" if b["on"] else "clock",
        "t0": round(t0, 3),
        "t1": round(t1, 3),
        "now": round(now, 3),
        "y_max": y_max,
        "over_cap": bool(proj_end is not None and proj_end > 100),
        "elapsed_pct": round(elapsed_pct, 1),
        "resets_at": win.get("resets_at"),
        "reference": _thin(reference),
        "observed": _thin(observed),
        "projection": _thin(projection),
    }


def build(usage: Dict[str, Any], limits: Dict[str, Any],
          keys: Sequence[str] = PANEL_KEYS,
          priors: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The whole panel payload. Both inputs come from the server's own cache,
    so this adds no network call and no extra ingest.

    `keys` picks which windows and in what order. It defaults to the two the
    notch panel stacks; the menu bar passes MENUBAR_KEYS to get the 5-hour
    window as well."""
    if not limits or not limits.get("ok"):
        return {"ok": False, "error": (limits or {}).get("error") or "no rate-limit data",
                "windows": []}
    weights = duty_weights((usage or {}).get("by_hour"))
    by_key = {w.get("key"): w for w in (limits.get("windows") or [])}
    out = []
    for key in keys:
        w = by_key.get(key)
        if not w:
            continue
        built = build_window(w, weights, (priors or {}).get(key))
        if built:
            out.append(built)
    return {
        "ok": bool(out),
        "checked_at": limits.get("checked_at"),
        "hours_per_day": (weights or {}).get("hours_per_day"),
        "hours_label": (weights or {}).get("label"),
        "sample_days": (weights or {}).get("days"),
        "windows": out,
    }


# ---------------------------------------------------------------- menu bar

# The menu bar shows the 5-hour window too. The panel leaves it out because two
# stacked charts is all a notch has room for; a dropdown has room, and the 5h
# window is the one that actually interrupts an afternoon. build_window already
# knows to keep windows under MIN_WORKDAY_WINDOW on the wall clock, so nothing
# else has to change to include it.
MENUBAR_KEYS = ("session", "weekly_all", "weekly_scoped")

# Everything build_window returns except the three plotted series.
MENUBAR_FIELDS = ("key", "label", "used_pct", "headline", "advice", "verdict", "tone",
                  "state", "source", "kicker", "detail", "basis", "resets_at",
                  "over_cap")


def _resets_in(ts: Optional[str]) -> Optional[int]:
    """Seconds from now until `ts`, floored at zero. The bar counts down between
    polls rather than asking again every second."""
    t = _parse(ts)
    if t is None:
        return None
    return max(0, int(t - _dt.datetime.now(_dt.timezone.utc).timestamp()))


_STATE_RANK = {"ok": 0, "watch": 1, "alert": 2}


def _severity(w: Dict[str, Any]) -> Tuple[int, float]:
    """How much a window deserves the bar's attention.

    Ordered the way chart_state thinks: alert outranks watch outranks ok, and
    among equals the fuller wins. The rule lives here so the icon's colour and
    the panel's colours can never disagree about which window is the problem.
    """
    return (_STATE_RANK.get(w.get("state") or "ok", 0), float(w.get("used_pct") or 0.0))


# A forecast made this early in a window is not worth interrupting anyone for.
# On the clock basis (the 5-hour window, or any window with the work-day model
# off) ten minutes in at 5% extrapolates to 150%.
ALERT_MIN_ELAPSED_PCT = 10.0


def alert_for(w: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """The one notification this window currently deserves, or None.

    The server decides WHETHER an alert exists and gives it a stable id. The
    client decides whether it has already shown that id. Keeping delivery
    state out of the server means an alert raised while no client was running
    is still shown when one starts, and "once" survives a server restart.

    The id is key + window + kind, so each kind fires at most once per window
    however often the forecast wobbles back and forth across 100.

    Two kinds:

    spent     the window reached its cap.
    forecast  the window is red for any other reason: at NEAR_LIMIT_PCT or
              above, or forecast to cross 100 before it resets.

    A forecast alert is withheld while the forecast is not yet this window's
    own. With source "history" the slope comes entirely from past windows; on
    an account that usually runs out, that would fire the moment every window
    opens, which is a calendar reminder, not news. Being near the limit is a
    measurement, not a forecast, so it is never withheld.
    """
    from . import history          # local: history does not import panel
    key, used = w.get("key"), float(w.get("used_pct") or 0)
    wid = history.window_id(w.get("resets_at"))
    if not key or not wid:
        return None
    label = w.get("label") or key
    reset = _fmt_reset(_parse(w.get("resets_at")))
    tail = ("Resets %s." % reset) if reset else ""

    if used >= 100:
        return {"id": "%s|%s|spent" % (key, wid), "kind": "spent",
                "title": "%s is spent" % label,
                "body": w.get("advice") or (("Nothing left until it resets %s." % reset)
                                             if reset else "Nothing left until it resets.")}

    if w.get("state") != "alert":
        return None
    if used < NEAR_LIMIT_PCT:
        if w.get("source") == "history":
            return None
        if float(w.get("elapsed_pct") or 0) < ALERT_MIN_ELAPSED_PCT:
            return None
    headline = w.get("headline") or "close to its limit"
    return {"id": "%s|%s|forecast" % (key, wid), "kind": "forecast",
            "title": "%s: %s" % (label, headline),
            "body": ("%.0f%% used. %s %s" % (used, tail, w.get("advice") or "")).strip()}


def menubar(usage: Dict[str, Any], limits: Dict[str, Any],
            priors: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The panel payload with the chart series dropped and a worst-window pick.

    A menu bar item polls this on a timer and needs two strings and a number,
    not 149 plotted points: without reference/observed/projection the response
    falls from roughly 9 KB to under 1 KB. `worst` is resolved here so a client
    that pins one window to the bar can still colour the icon by the window that
    is actually in trouble.
    """
    full = build(usage, limits, keys=MENUBAR_KEYS, priors=priors)
    if not full.get("ok"):
        return {"ok": False, "error": full.get("error"), "windows": [], "worst": None,
                "alerts": []}
    wins, alerts = [], []
    for w in full.get("windows") or []:
        c = {k: w[k] for k in MENUBAR_FIELDS if k in w}
        c["resets_in"] = _resets_in(w.get("resets_at"))
        wins.append(c)
        a = alert_for(w)
        if a:
            alerts.append(a)
    worst = max(wins, key=_severity) if wins else None
    return {
        "ok": True,
        "checked_at": full.get("checked_at"),
        "hours_per_day": full.get("hours_per_day"),
        "hours_label": full.get("hours_label"),
        "sample_days": full.get("sample_days"),
        "worst": (worst or {}).get("key"),
        "windows": wins,
        "alerts": alerts,
    }
