#!/usr/bin/env python3
"""
parse_usage.py - aggregates the local SQLite index into the shapes the UI needs.

Reads:       ~/.usemeup/usage.db  (built by store.py from local transcripts)
Network:     none
Credentials: none
"""
import datetime
import json
import os

from . import config
from . import coverage
from . import pricing as pricing_mod
from . import store

HERE = os.path.dirname(os.path.abspath(__file__))

METRICS = ("input", "output", "thinking", "cache_read", "cache_write",
           "cache_5m", "cache_1h", "web_search", "calls")


def _pricing():
    """Live price table when available, cache or bundled table otherwise."""
    return pricing_mod.load()[0]


def _price_for(model, prices):
    return pricing_mod.price_for(model, prices)


def _cost(row, price):
    if not price or price.get("input") is None:
        return None
    per = lambda n, k: (n or 0) / 1000000.0 * (price.get(k) or 0)
    return (per(row["input"], "input") + per(row["output"], "output")
            + per(row["cache_5m"], "cache_write_5m")
            + per(row["cache_1h"], "cache_write_1h")
            + per(row["cache_read"], "cache_read"))


def _blank():
    d = {k: 0 for k in METRICS}
    d.update(cost=0.0, priced=True, unpriced_calls=0)
    return d


def _add(acc, row, cost):
    for k in METRICS:
        acc[k] += row[k]
    if cost is None:
        acc["priced"] = False
        acc["unpriced_calls"] += row["calls"]
    else:
        acc["cost"] += cost


def project_dirs():
    out = []
    if not os.path.isdir(config.CLI_ROOT):
        return out
    for d in sorted(os.listdir(config.CLI_ROOT)):
        full = os.path.join(config.CLI_ROOT, d)
        if not os.path.isdir(full):
            continue
        n = len([f for f in os.listdir(full) if f.endswith(".jsonl")])
        out.append({"slug": d, "name": config.redact(store._pretty_project(d)), "files": n,
                    "excluded": config.excluded(full)})
    return out


def sessions(limit=25):
    """Most expensive sessions. Subagent calls are folded into their parent."""
    prices = _pricing()
    db = store.connect()
    rows = db.execute("""
        SELECT session, session_label, source,
               MIN(ts) started, MAX(ts) ended, COUNT(*) calls,
               SUM(is_subagent) subagent_calls,
               SUM(input) input, SUM(output) output, SUM(thinking) thinking,
               SUM(cache_read) cache_read, SUM(cache_write) cache_write,
               SUM(cache_5m) cache_5m, SUM(cache_1h) cache_1h,
               SUM(web_search) web_search
        FROM calls WHERE session IS NOT NULL
        GROUP BY session
    """).fetchall()

    # Cost has to be computed per model, so re-price each session's model mix.
    per_model = {}
    for r in db.execute("""
        SELECT session, model,
               SUM(input) input, SUM(output) output,
               SUM(cache_read) cache_read, SUM(cache_5m) cache_5m, SUM(cache_1h) cache_1h
        FROM calls WHERE session IS NOT NULL GROUP BY session, model"""):
        per_model.setdefault(r["session"], []).append(dict(r))
    db.close()

    out = []
    for r in rows:
        cost, models = 0.0, {}
        for pm in per_model.get(r["session"], []):
            c = _cost(pm, _price_for(pm["model"], prices))
            if c:
                cost += c
                models[pm["model"]] = round(c, 4)
        dur = 0
        try:
            a = datetime.datetime.fromisoformat(r["started"].replace("Z", "+00:00"))
            b = datetime.datetime.fromisoformat(r["ended"].replace("Z", "+00:00"))
            dur = int((b - a).total_seconds())
        except Exception:
            pass
        out.append({
            "session": r["session"][:8],
            "label": config.redact(r["session_label"] or "unknown"),
            "source": r["source"],
            "started": r["started"], "ended": r["ended"], "duration_seconds": dur,
            "calls": r["calls"], "subagent_calls": r["subagent_calls"] or 0,
            "output": r["output"] or 0, "cache_read": r["cache_read"] or 0,
            "cost": round(cost, 4),
            "top_model": max(models, key=models.get) if models else None,
        })
    out.sort(key=lambda s: -s["cost"])
    return out[:limit]


def limit_by_day(days=120):
    """Per-calendar-day movement of each weekly rate-limit window, from samples.

    Same day cut as the transcript charts (config.LOCAL_TZ), so the two can sit
    side by side. Empty when nothing has been sampled yet.
    """
    since = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(days=days + 1)).isoformat()
    try:
        samples, names = store.all_limit_samples(since)
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, e)}
    return coverage.build(samples, names, config.LOCAL_TZ)


def build(block_days=30, day_limit=120):
    prices = _pricing()
    db = store.connect()

    grouped = db.execute("""
        SELECT day, model, source, project,
               SUM(input) input, SUM(output) output, SUM(thinking) thinking,
               SUM(cache_read) cache_read, SUM(cache_write) cache_write,
               SUM(cache_5m) cache_5m, SUM(cache_1h) cache_1h,
               SUM(web_search) web_search, COUNT(*) calls
        FROM calls GROUP BY day, model, source, project
    """).fetchall()

    totals = _blank()
    by_day, by_model, by_project, by_source = {}, {}, {}, {}
    by_day_model, by_day_source = {}, {}
    unpriced = set()

    for r in grouped:
        row = {k: (r[k] or 0) for k in METRICS}
        price = _price_for(r["model"] or "", prices)
        cost = _cost(row, price)
        if cost is None:
            unpriced.add(r["model"])
        proj = config.redact(r["project"])
        _add(totals, row, cost)
        for bucket, key in ((by_day, r["day"]), (by_model, r["model"]),
                            (by_project, proj), (by_source, r["source"])):
            _add(bucket.setdefault(key, _blank()), row, cost)
        _add(by_day_model.setdefault(r["day"], {}).setdefault(r["model"], _blank()), row, cost)
        _add(by_day_source.setdefault(r["day"], {}).setdefault(r["source"], _blank()), row, cost)

    # When each model first and last appeared. The UI keys its colours to this,
    # so a model keeps one colour across every range instead of changing with
    # its cost rank.
    model_span = {r["model"]: {"first": r["a"], "last": r["b"]} for r in db.execute(
        "SELECT model, MIN(day) a, MAX(day) b FROM calls GROUP BY model")}

    since = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(days=block_days)).isoformat()
    blocks, cur = [], None
    for r in db.execute(
            "SELECT ts, model, total tok, input, output, cache_read, cache_5m, cache_1h"
            " FROM calls WHERE ts >= ? ORDER BY ts", (since,)):
        t = datetime.datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
        if cur is None or t >= cur["end"]:
            cur = {"start": t, "end": t + datetime.timedelta(hours=5),
                   "tokens": 0, "calls": 0, "cost": 0.0}
            blocks.append(cur)
        cur["tokens"] += r["tok"] or 0
        cur["calls"] += 1
        c = _cost({"input": r["input"], "output": r["output"], "cache_read": r["cache_read"],
                   "cache_5m": r["cache_5m"], "cache_1h": r["cache_1h"]},
                  _price_for(r["model"] or "", prices))
        if c:
            cur["cost"] += c

    span = db.execute("SELECT MIN(day) a, MAX(day) b, COUNT(*) n FROM calls").fetchone()
    db.close()

    keep = set(sorted(by_day)[-day_limit:])
    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "timezone": str(config.LOCAL_TZ),
        "pricing": pricing_mod.load()[1],
        "demo": config.DEMO,
        "totals": totals,
        "by_day": {d: v for d, v in by_day.items() if d in keep},
        "by_model": by_model,
        "by_project": by_project,
        "by_source": by_source,
        "by_day_model": {d: v for d, v in by_day_model.items() if d in keep},
        "by_day_source": {d: v for d, v in by_day_source.items() if d in keep},
        "model_span": model_span,
        "limit_by_day": limit_by_day(day_limit),
        "sessions": sessions(),
        "unpriced_models": sorted(unpriced),
        "blocks": [{"start": b["start"].isoformat(), "end": b["end"].isoformat(),
                    "tokens": b["tokens"], "calls": b["calls"], "cost": round(b["cost"], 4)}
                   for b in blocks[-60:]],
        "project_dirs": project_dirs(),
        "excluded_slugs": list(config.EXCLUDE_SLUGS),
        "record_count": span["n"] or 0,
        "day_count": len(by_day),
        "first_day": span["a"], "last_day": span["b"],
        "days_shown": len(keep),
    }


if __name__ == "__main__":
    d = build()
    print("records: %s | days: %d (%s -> %s) | tz %s" % (
        format(d["record_count"], ","), d["day_count"], d["first_day"], d["last_day"],
        d["timezone"]))
    print("notional total: $%.2f" % d["totals"]["cost"])
    print("\ntop sessions:")
    for s in d["sessions"][:8]:
        print("   $%8.2f  %5d calls (%3d subagent)  %-28s %s" % (
            s["cost"], s["calls"], s["subagent_calls"], s["label"][:28], s["started"][:16]))
