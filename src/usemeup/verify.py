#!/usr/bin/env python3
"""
verify.py - prove the dashboard's numbers by recounting from raw transcripts.

    python3 verify.py            # check every day
    python3 verify.py --days 30  # only the most recent 30 days
    python3 verify.py --day 2026-09-02

Exits 0 when every day matches, 1 when anything is off.

Why this exists
---------------
Every tool in this category claims its numbers are right. This one can show its
work. The recount below deliberately shares NO code with the tool: it re-reads
the transcripts, re-implements deduplication and pricing from scratch, and then
compares against the database. A check that imports the thing it is checking can
only confirm the code is self-consistent, which is exactly how two real bugs
survived in this project:

  * deduplication kept the first copy of a streamed response (output_tokens=1)
    instead of the largest (output_tokens=56,354)
  * days were bucketed by UTC date rather than the user's own timezone,
    misplacing about 10% of calls

Both were found by a recount like this one, not by reading the code.

What this proves, and what it does not
-------------------------------------
It proves the COUNTING: which records exist, how duplicates collapse, which day
each call lands on, and that the index agrees with the transcripts. That is where
the bugs were.

It does not independently prove the PRICE TABLE. Both sides deliberately read the
same prices, because a recount that used different prices would report a mismatch
on every day and tell you nothing about counting. Prices come from the LiteLLM
feed, whose first-party Anthropic rows were cross-checked against Anthropic's
published pricing page; run `usemeup prices` to see the table and its source.

Shared with the tool: transcript paths, timezone, the exclusion rule, the
placeholder-message rule, and the price table. Independent: reading,
deduplication, bucketing and aggregation.

Sharing the exclusion rule matters: this tool can make its own throwaway calls to
refresh an expired token, and when the recount and the index disagreed about
whether to count those, verify.py reported a mismatch that was not real. The rule
is an exact match on the refresh project folder (config.excluded), applied to
both transcript roots here and in store.py alike.

One definition to know when checking a single day by hand: a call is dated by
the timestamp of its largest recorded copy. For a streamed response that is the
final chunk, so a call that starts at 11:59 PM and finishes at 12:01 AM belongs
to the second day, in the index and in this recount both.
"""
import argparse
import datetime
import json
import os
import sqlite3
import sys

from . import config
from . import pricing as pricing_mod

def load_prices():
    return pricing_mod.load()[0]


def price_for(model, prices):
    return pricing_mod.price_for(model, prices)


def recount():
    """Independent pass over the raw transcripts.

    Deduplication: identity is (requestId, message.id); keep the record with the
    largest token total, because a streamed response is written repeatedly as it
    grows and resumed sessions replay compacted copies.
    """
    roots = [config.CLI_ROOT]
    if config.COWORK_ROOT:
        roots.append(config.COWORK_ROOT)

    best = {}
    files = 0
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _dn, filenames in os.walk(root):
            if config.excluded(dirpath):
                continue
            for fn in filenames:
                if not fn.endswith(".jsonl") or fn == "audit.jsonl":
                    continue
                files += 1
                try:
                    fh = open(os.path.join(dirpath, fn), errors="replace")
                except OSError:
                    continue
                with fh:
                    for line in fh:
                        if '"usage"' not in line:
                            continue
                        try:
                            d = json.loads(line)
                        except Exception:
                            continue
                        m = d.get("message")
                        if not isinstance(m, dict):
                            continue
                        u = m.get("usage")
                        if not isinstance(u, dict) or not d.get("timestamp"):
                            continue
                        if config.synthetic(m.get("model")):
                            continue      # placeholder, not an API call (same rule as store.py)
                        inp = u.get("input_tokens") or 0
                        out = u.get("output_tokens") or 0
                        cr = u.get("cache_read_input_tokens") or 0
                        cw = u.get("cache_creation_input_tokens") or 0
                        total = inp + out + cr + cw
                        key = (d.get("requestId"), m.get("id"))
                        prev = best.get(key)
                        if prev is None or total > prev["total"]:
                            cc = u.get("cache_creation") or {}
                            best[key] = {
                                "total": total,
                                "day": config.local_day(d["timestamp"]),
                                "model": m.get("model") or "unknown",
                                "input": inp, "output": out,
                                "cache_read": cr,
                                "cache_5m": cc.get("ephemeral_5m_input_tokens") or 0,
                                "cache_1h": cc.get("ephemeral_1h_input_tokens") or 0,
                            }
    return best, files


def aggregate(best, prices):
    days = {}
    for r in best.values():
        d = days.setdefault(r["day"], {"calls": 0, "output": 0, "cache_read": 0, "cost": 0.0})
        d["calls"] += 1
        d["output"] += r["output"]
        d["cache_read"] += r["cache_read"]
        p = price_for(r["model"], prices)
        if p and p.get("input") is not None:
            per = lambda n, k: (n or 0) / 1000000.0 * (p.get(k) or 0)
            d["cost"] += (per(r["input"], "input") + per(r["output"], "output")
                          + per(r["cache_5m"], "cache_write_5m")
                          + per(r["cache_1h"], "cache_write_1h")
                          + per(r["cache_read"], "cache_read"))
    return days


def from_db():
    if not os.path.exists(config.DB_PATH):
        return None
    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute(
            "SELECT day, COUNT(*) calls, SUM(output) output, SUM(cache_read) cache_read,"
            " model, SUM(input) input, SUM(cache_5m) cache_5m, SUM(cache_1h) cache_1h"
            " FROM calls GROUP BY day, model").fetchall()
    except sqlite3.OperationalError as e:
        db.close()
        print("could not read the index (%s). Run `python3 store.py` first." % e)
        return None
    db.close()
    return rows


def main():
    ap = argparse.ArgumentParser(description="Recount usage from raw transcripts and "
                                             "compare against the index.")
    ap.add_argument("--days", type=int, default=0, help="only the most recent N days")
    ap.add_argument("--day", help="a single YYYY-MM-DD to check")
    ap.add_argument("--tolerance", type=float, default=0.01, help="dollar tolerance")
    args = ap.parse_args()

    prices = load_prices()
    print("recounting from raw transcripts (importing none of the tool's logic)…")
    best, files = recount()
    mine = aggregate(best, prices)
    print("  %s files read, %s unique calls after dedup, %d days"
          % (format(files, ","), format(len(best), ","), len(mine)))

    rows = from_db()
    if rows is None:
        return 1

    theirs = {}
    for r in rows:
        t = theirs.setdefault(r["day"], {"calls": 0, "output": 0, "cache_read": 0, "cost": 0.0})
        t["calls"] += r["calls"]
        t["output"] += r["output"] or 0
        t["cache_read"] += r["cache_read"] or 0
        p = price_for(r["model"], prices)
        if p and p.get("input") is not None:
            per = lambda n, k: (n or 0) / 1000000.0 * (p.get(k) or 0)
            t["cost"] += (per(r["input"], "input") + per(r["output"], "output")
                          + per(r["cache_5m"], "cache_write_5m")
                          + per(r["cache_1h"], "cache_write_1h")
                          + per(r["cache_read"], "cache_read"))

    days = sorted(set(mine) | set(theirs))
    if args.day:
        days = [d for d in days if d == args.day]
    elif args.days:
        days = days[-args.days:]

    bad = []
    for d in days:
        a, b = mine.get(d), theirs.get(d)
        if not a or not b:
            bad.append((d, "missing from %s" % ("the index" if a else "the recount")))
            continue
        if a["calls"] != b["calls"]:
            bad.append((d, "calls %d vs %d" % (a["calls"], b["calls"])))
        elif a["output"] != b["output"]:
            bad.append((d, "output %d vs %d" % (a["output"], b["output"])))
        elif abs(a["cost"] - b["cost"]) > args.tolerance:
            bad.append((d, "cost $%.4f vs $%.4f" % (a["cost"], b["cost"])))

    tm = sum(v["cost"] for k, v in mine.items() if k in set(days))
    tt = sum(v["cost"] for k, v in theirs.items() if k in set(days))
    print("\nchecked %d day(s)" % len(days))
    print("  recount total : $%.2f" % tm)
    print("  index total   : $%.2f" % tt)

    if bad:
        print("\nMISMATCH on %d day(s):" % len(bad))
        for d, why in bad[:25]:
            print("  %s  %s" % (d, why))
        if len(bad) > 25:
            print("  … and %d more" % (len(bad) - 25))
        print("\nThe index is stale or wrong. `python3 store.py` re-ingests; if a day still")
        print("disagrees after that, the bug is real and the recount is the number to trust.")
        return 1

    print("\nOK. Every checked day matches on calls, output tokens and cost.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
