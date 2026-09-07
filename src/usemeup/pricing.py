#!/usr/bin/env python3
"""
pricing.py - resolve per-model prices, preferring a live source over a stale file.

A hand-maintained price table is wrong the day a new model ships: the model shows
up as `unpriced` and the totals quietly understate. This module fetches the
community-maintained LiteLLM price database, caches it, and falls back to the
bundled pricing.json when the network is unavailable or disabled.

NETWORK: this is the only file besides rate_limits.py that makes a request, and
it is a plain GET of a public file on raw.githubusercontent.com. Nothing about
you is sent: no token, no identifiers, no query parameters. Set USEMEUP_OFFLINE=1
to skip it entirely and use the bundled table.

CORRECTNESS: only rows whose litellm_provider is "anthropic" are used. The
us.anthropic.* rows in that file are Amazon Bedrock regional prices carrying a
10% premium, and using them would overstate every figure by that much.
"""
import json
import os
import time
import urllib.request

from . import config

SOURCE_URL = ("https://raw.githubusercontent.com/BerriAI/litellm/main/"
              "model_prices_and_context_window.json")
CACHE_PATH = os.path.join(os.path.dirname(config.DB_PATH), "pricing-cache.json")
BUNDLED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pricing.json")
TTL_SECONDS = 24 * 3600
M = 1000000.0

_memo = {"table": None, "meta": None, "at": 0.0}


def _bundled():
    try:
        with open(BUNDLED) as f:
            blob = json.load(f)
        return blob.get("models", {}), {"source": "bundled",
                                        "note": blob.get("_source", ""),
                                        "models": len(blob.get("models", {}))}
    except Exception as e:
        return {}, {"source": "none", "error": "%s: %s" % (type(e).__name__, e), "models": 0}


def _convert(raw):
    """LiteLLM rows -> our $/Mtok shape, first-party Anthropic only."""
    out = {}
    for key, v in (raw or {}).items():
        if not isinstance(v, dict):
            continue
        if v.get("litellm_provider") != "anthropic":
            continue          # us.anthropic.* etc. are Bedrock, priced 10% higher
        if "claude" not in key.lower():
            continue
        inp = v.get("input_cost_per_token")
        outp = v.get("output_cost_per_token")
        if inp is None or outp is None:
            continue
        cw5 = v.get("cache_creation_input_token_cost")
        cw1 = v.get("cache_creation_input_token_cost_above_1hr")
        cr = v.get("cache_read_input_token_cost")
        out[key] = {
            "input": inp * M,
            "output": outp * M,
            # Anthropic's documented multipliers, used only when the feed omits them.
            "cache_write_5m": (cw5 * M) if cw5 is not None else inp * M * 1.25,
            "cache_write_1h": (cw1 * M) if cw1 is not None else inp * M * 2.0,
            "cache_read": (cr * M) if cr is not None else inp * M * 0.1,
        }
    return out


def _read_cache():
    try:
        with open(CACHE_PATH) as f:
            blob = json.load(f)
        if not isinstance(blob.get("models"), dict) or not blob["models"]:
            return None
        return blob
    except Exception:
        return None


def _write_cache(models):
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"fetched_at": time.time(), "source_url": SOURCE_URL,
                       "models": models}, f)
        os.replace(tmp, CACHE_PATH)
    except Exception:
        pass          # a cache we cannot write is not a reason to fail


def _fetch():
    req = urllib.request.Request(SOURCE_URL, headers={"user-agent": "usemeup-local/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def load(force=False):
    """Return (table, meta). Never raises; always returns a usable table."""
    now = time.time()
    if not force and _memo["table"] and now - _memo["at"] < 300:
        return _memo["table"], _memo["meta"]

    cached = _read_cache()
    fresh = cached and (now - cached.get("fetched_at", 0)) < TTL_SECONDS

    if config.OFFLINE:
        if cached:
            table, meta = cached["models"], {
                "source": "cache (offline mode)", "models": len(cached["models"]),
                "age_hours": round((now - cached.get("fetched_at", 0)) / 3600, 1)}
        else:
            table, meta = _bundled()
            meta["source"] = "bundled (offline mode, no cache yet)"
    elif fresh and not force:
        table, meta = cached["models"], {
            "source": "cache", "models": len(cached["models"]),
            "age_hours": round((now - cached["fetched_at"]) / 3600, 1)}
    else:
        try:
            converted = _convert(_fetch())
            if not converted:
                raise ValueError("no first-party anthropic rows in the feed")
            _write_cache(converted)
            table, meta = converted, {"source": "live", "models": len(converted),
                                      "age_hours": 0.0}
        except Exception as e:
            if cached:
                table, meta = cached["models"], {
                    "source": "cache (refresh failed)",
                    "error": "%s: %s" % (type(e).__name__, e),
                    "models": len(cached["models"]),
                    "age_hours": round((now - cached.get("fetched_at", 0)) / 3600, 1)}
            else:
                table, meta = _bundled()
                meta["source"] = "bundled (fetch failed)"
                meta["error"] = "%s: %s" % (type(e).__name__, e)

    # The bundled table is a floor, not a ceiling: keep any model the live feed
    # does not carry rather than regressing to `unpriced`.
    base, _ = _bundled()
    merged = dict(base)
    merged.update(table)
    meta["models"] = len(merged)
    meta["url"] = SOURCE_URL

    _memo.update(table=merged, meta=meta, at=now)
    return merged, meta


def price_for(model, table=None):
    """Longest matching model-id prefix wins, so dated ids resolve correctly."""
    table = table if table is not None else load()[0]
    cands = [k for k in table if (model or "").startswith(k)]
    return table[max(cands, key=len)] if cands else None
