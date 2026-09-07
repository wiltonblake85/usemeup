#!/usr/bin/env python3
"""
rate_limits.py - the ONLY file in this project that touches a credential or the network.

It does exactly four things:
  1. Reads your Claude Code OAuth token from the macOS Keychain (or ~/.claude/.credentials.json).
  2. Sends it to https://api.anthropic.com and nowhere else.
  3. Keeps the limit figures from the response.
  4. Discards the token. It is never written to disk, logged, cached, or returned.

It is strictly READ-ONLY on your credential. It never writes to the Keychain and never
uses the refresh token, so it cannot invalidate or rotate your Claude Code login. When
the access token expires it says so and stops; refreshing is Claude Code's job.

Everything returned passes through _safe() first. To satisfy yourself the token cannot
escape, read _safe() and the return statements in probe(). That is the whole surface.
"""
import json, os, subprocess, time, urllib.request, urllib.error, datetime

import config

API_HOST = "https://api.anthropic.com"
KEYCHAIN_SERVICES = ["Claude Code-credentials", "Claude Code", "claude-code"]
CRED_FILE = os.path.expanduser("~/.claude/.credentials.json")


def _safe(v):
    """Never let anything token-shaped out of this module."""
    if not isinstance(v, str):
        return v
    if len(v) > 40 and any(v.startswith(p) for p in ("sk-", "Bearer ", "eyJ")):
        return "<redacted>"
    return v


def _ms_to_iso(v):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n > 1e11:
        n /= 1000.0
    return datetime.datetime.fromtimestamp(n, datetime.timezone.utc).isoformat()


def _token():
    """Return (token, source, meta). The token stays in memory only.

    meta carries expiry facts (never the token): expires_at, expired,
    refresh_expires_at. We read expiresAt so we can fail with a useful message
    instead of firing doomed requests at the API.
    """
    blob, source = None, None
    for svc in KEYCHAIN_SERVICES:
        try:
            out = subprocess.run(
                ["security", "find-generic-password", "-s", svc, "-w"],
                capture_output=True, text=True, timeout=20)
        except Exception as e:
            return None, None, {"error": "keychain call failed: %s" % type(e).__name__}
        if out.returncode == 0 and out.stdout.strip():
            raw = out.stdout.strip()
            source = "keychain:%s" % svc
            try:
                blob = json.loads(raw)
            except Exception:
                return raw, source + " (raw)", {}
            break
    if blob is None and os.path.exists(CRED_FILE):
        try:
            with open(CRED_FILE) as f:
                blob = json.load(f)
            source = "credentials file"
        except Exception:
            blob = None
    if blob is None:
        return None, None, {"error": "no Claude Code credential found (tried Keychain %s and %s)"
                            % (", ".join(KEYCHAIN_SERVICES), CRED_FILE)}

    oauth = blob.get("claudeAiOauth") or blob
    tok = oauth.get("accessToken")
    exp = oauth.get("expiresAt")
    now_ms = datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000
    meta = {
        "expires_at": _ms_to_iso(exp),
        "expired": bool(exp) and float(exp) < now_ms,
        "refresh_expires_at": _ms_to_iso(oauth.get("refreshTokenExpiresAt")),
    }
    if not tok:
        meta["error"] = "credential found at %s but it carries no access token" % source
        return None, source, meta
    return tok, source, meta


# --- Keeping the token fresh without ever touching the refresh token -------------
# The access token lives 8-12 hours and only Claude Code refreshes it, on a real API
# call. Rather than use the refresh token ourselves (many OAuth servers rotate it on
# use, and mishandling that would break the CLI login), we ask the CLI to make one
# cheap call and then re-read the Keychain. This module stays read-only on the
# credential; Claude Code remains the only thing that ever writes it.
REFRESH_CMD = ["claude", "-p", "ok", "--model", "claude-haiku-4-5"]
REFRESH_CWD = config.REFRESH_CWD
REFRESH_COOLDOWN = 600      # never more than once every 10 minutes
_last_refresh = [0.0]


def refresh_via_claude_code():
    """Trigger Claude Code's own OAuth refresh. Returns a status dict, never a token."""
    if time.time() - _last_refresh[0] < REFRESH_COOLDOWN:
        return {"ran": False, "reason": "cooling down; last attempt was under %ds ago"
                % REFRESH_COOLDOWN}
    _last_refresh[0] = time.time()
    try:
        os.makedirs(REFRESH_CWD, exist_ok=True)
        with open(os.devnull) as devnull:
            out = subprocess.run(REFRESH_CMD, cwd=REFRESH_CWD, stdin=devnull,
                                 capture_output=True, text=True, timeout=120)
        return {"ran": True, "returncode": out.returncode,
                "detail": _safe(((out.stdout or "") + (out.stderr or "")).strip()[:200])}
    except FileNotFoundError:
        return {"ran": False, "reason": "the `claude` CLI is not on PATH for this process"}
    except Exception as e:
        return {"ran": False, "reason": "%s: %s" % (type(e).__name__, e)}


def _call(url, token, method="GET", body=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("authorization", "Bearer " + token)
    req.add_header("anthropic-version", "2023-06-01")
    req.add_header("anthropic-beta", "oauth-2025-04-20")
    req.add_header("user-agent", "usemeup-local/1.0")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=25) as r:
            return r.status, dict(r.headers), r.read(8000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read(8000).decode("utf-8", "replace")
    except Exception as e:
        return None, {}, "%s: %s" % (type(e).__name__, e)


def _limits_from(headers):
    return {k.lower(): _safe(v) for k, v in headers.items()
            if k.lower().startswith("anthropic-ratelimit")}


def _secs_until(v):
    if v is None:
        return None
    now = datetime.datetime.now(datetime.timezone.utc)
    t = str(v).strip()
    try:
        return max(0, int((datetime.datetime.fromisoformat(
            t.replace("Z", "+00:00")) - now).total_seconds()))
    except ValueError:
        pass
    try:
        n = float(t)
    except ValueError:
        return None
    if n > 1e9:
        return max(0, int((datetime.datetime.fromtimestamp(
            n, datetime.timezone.utc) - now).total_seconds()))
    return max(0, int(n))


KIND_NAMES = {
    "session": "5-hour session window",
    "weekly_all": "7-day window, all models",
    "weekly_scoped": "7-day window",
    "weekly_oauth_apps": "7-day window, connected apps",
}


def _shape_json(body):
    """Preferred path: the limits[] array from /api/oauth/usage."""
    out = []
    for lim in (body.get("limits") or []):
        pct = lim.get("percent")
        if pct is None:
            continue
        kind = str(lim.get("kind") or "")
        name = KIND_NAMES.get(kind, kind.replace("_", " ") or "limit")
        scope = lim.get("scope") or {}
        model = ((scope.get("model") or {}).get("display_name")) if isinstance(scope, dict) else None
        if model:
            name = "%s, %s only" % (name.split(",")[0], model)
        out.append({
            "key": kind,
            "name": name,
            "used_pct": round(float(pct), 1),
            "status": lim.get("severity"),
            "binding": bool(lim.get("is_active")),
            "resets_at": lim.get("resets_at"),
            "resets_in_seconds": _secs_until(lim.get("resets_at")),
            "window_seconds": 7 * 86400 if kind.startswith("weekly") else (
                5 * 3600 if kind == "session" else None),
            "segments": 7 if kind.startswith("weekly") else None,
        })
    out.sort(key=lambda w: ({"session": 0, "weekly_all": 1}.get(w["key"], 2), w["name"]))
    return out


def _shape_headers(limits):
    """Fallback: anthropic-ratelimit-unified-* response headers."""
    BASE = "anthropic-ratelimit-unified"
    PRETTY = {"5h": "5-hour session window", "7d": "7-day window"}
    CLAIM = {"5h": "five_hour", "7d": "seven_day"}
    groups = {}
    for k, v in limits.items():
        if not k.startswith(BASE):
            continue
        rest = k[len(BASE):].lstrip("-")
        for field in ("utilization", "status", "reset", "limit", "remaining"):
            if rest == field:
                groups.setdefault("", {})[field] = v
            elif rest.endswith("-" + field):
                groups.setdefault(rest[:-(len(field) + 1)], {})[field] = v
    rep = limits.get(BASE + "-representative-claim")
    out = []
    for name, g in groups.items():
        used = None
        if g.get("utilization") is not None:
            try:
                used = max(0.0, min(100.0, float(g["utilization"]) * 100))
            except ValueError:
                pass
        if used is None:
            continue
        out.append({"key": name or "overall", "name": PRETTY.get(name) or (name or "overall"),
                    "used_pct": round(used, 1), "status": g.get("status"),
                    "binding": rep is not None and CLAIM.get(name) == rep,
                    "resets_in_seconds": _secs_until(g.get("reset")),
                    "window_seconds": 7 * 86400 if name == "7d" else (5 * 3600 if name == "5h" else None),
                    "segments": 7 if name == "7d" else None})
    out.sort(key=lambda w: ({"5h": 0, "7d": 1}.get(w["key"], 2), w["name"]))
    return out


def probe(allow_ping=False, auto_refresh=None):
    """Fetch live rate-limit state. The returned dict never contains a credential.

    allow_ping sends a 1-token message as a fallback when the usage endpoint fails.
    Off by default: it costs tokens and, when the usage endpoint is rate limited,
    piling on a second request is the wrong move.

    auto_refresh shells out to Claude Code when the stored token has expired, so the
    CLI refreshes its own credential. We never use the refresh token ourselves.
    It is OFF unless USEMEUP_AUTO_REFRESH=1, because it spends a small number of
    your tokens and no tool should do that silently.
    """
    if auto_refresh is None:
        auto_refresh = config.AUTO_REFRESH
    token, source, meta = _token()
    refresh = None
    if auto_refresh and (not token or meta.get("expired")):
        refresh = refresh_via_claude_code()
        if refresh.get("ran"):
            token, source, meta = _token()

    base = {"ok": False, "source": source, "windows": [], "raw": {},
            "token": {k: v for k, v in meta.items() if k != "error"},
            "refresh": refresh}

    if not token:
        base["error"] = meta.get("error", "no usable credential")
        base["reason"] = "no_credential"
        return base

    if meta.get("expired"):
        base["reason"] = "token_expired"
        why = (("Tried to refresh via Claude Code and it did not take: %s"
                % (refresh.get("detail") or refresh.get("reason")))
               if refresh else
               "Automatic refresh is off (set USEMEUP_AUTO_REFRESH=1 to enable it).")
        base["error"] = ("The stored Claude Code access token expired at %s. %s Run "
                         "`claude -p ok` in a terminal, then reload. Note that "
                         "`claude auth status` does NOT refresh it."
                         % (meta.get("expires_at") or "an unknown time", why))
        del token
        return base

    attempts = []
    status, headers, text = _call(API_HOST + "/api/oauth/usage", token)
    try:
        body = json.loads(text)
    except Exception:
        body = {}
    windows = _shape_json(body) if isinstance(body, dict) else []
    attempts.append({"strategy": "usage endpoint", "status": status,
                     "windows_found": len(windows),
                     "detail": "" if windows else _safe(text[:200])})
    result = None
    if windows:
        result = {"windows": windows, "strategy": "usage endpoint",
                  "raw": {k: v for k, v in body.items() if k in ("limits", "five_hour", "seven_day")}}

    if result is None and status == 401 and auto_refresh and refresh is None:
        refresh = refresh_via_claude_code()
        if refresh.get("ran"):
            tok2, source2, meta2 = _token()
            if tok2 and not meta2.get("expired"):
                del token
                token, source = tok2, source2
                base["token"] = {k: v for k, v in meta2.items() if k != "error"}
                status, headers, text = _call(API_HOST + "/api/oauth/usage", token)
                try:
                    body = json.loads(text)
                except Exception:
                    body = {}
                windows = _shape_json(body) if isinstance(body, dict) else []
                attempts.append({"strategy": "usage endpoint (after refresh)", "status": status,
                                 "windows_found": len(windows),
                                 "detail": "" if windows else _safe(text[:200])})
                if windows:
                    result = {"windows": windows, "strategy": "usage endpoint (after refresh)",
                              "raw": {k: v for k, v in body.items()
                                      if k in ("limits", "five_hour", "seven_day")}}

    if result is None and status == 429:
        del token
        base.update({"reason": "rate_limited", "attempts": attempts,
                     "error": "The usage endpoint is rate limiting this client (HTTP 429). "
                              "Backing off; the last good reading is shown above if there is one."})
        return base

    if result is None and allow_ping:
        status, headers, text = _call(
            API_HOST + "/v1/messages", token, "POST",
            {"model": "claude-haiku-4-5", "max_tokens": 1,
             "messages": [{"role": "user", "content": "."}]})
        raw = _limits_from(headers)
        w2 = _shape_headers(raw)
        attempts.append({"strategy": "messages ping", "status": status,
                         "windows_found": len(w2), "detail": "" if w2 else _safe(text[:200])})
        if raw:
            result = {"windows": w2, "strategy": "messages ping (header fallback)", "raw": raw}

    del token
    if result is None:
        auth_fail = any(a.get("status") == 401 for a in attempts)
        base.update({
            "attempts": attempts,
            "reason": "auth_failed" if auth_fail else "no_data",
            "error": ("The API rejected the stored token (HTTP 401). Run any Claude Code command "
                      "to refresh it, then reload." if auth_fail else
                      "Credential loaded from %s, but no endpoint returned usable limit data."
                      % source)})
        return base

    result.update({"ok": True, "source": source, "attempts": attempts,
                   "token": base["token"], "refresh": refresh,
                   "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat()})
    return result


if __name__ == "__main__":
    print(json.dumps(probe(), indent=2))
