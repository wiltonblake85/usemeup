#!/usr/bin/env python3
"""
rate_limits.py - the ONLY file in this project that touches a credential or the network.

With USEMEUP_SOURCE=statusline it touches neither: probe() returns what Claude
Code's own status line handed over (see statusline.py) and nothing below runs.

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

from . import config
from . import statusline

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


def _as_ms(v):
    """A stored timestamp as epoch milliseconds, whichever unit it was saved in."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if n > 1e11 else n * 1000.0


def _ms_to_iso(v):
    n = _as_ms(v)
    if n is None:
        return None
    # Whole seconds: the Mac app parses these with ISO8601DateFormatter, which
    # does not reliably take six fractional digits.
    return datetime.datetime.fromtimestamp(int(n / 1000.0), datetime.timezone.utc).isoformat()


def _local_time(iso):
    """An ISO time as this Mac's clock reads it: "Tue Sep 29 at 12:28 AM"."""
    if not iso:
        return None
    try:
        t = datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None
    return "%s at %s" % (t.strftime("%a %b ") + str(t.day),
                         t.strftime("%I:%M %p").lstrip("0"))


def _token():
    """Return (token, source, meta). The token stays in memory only.

    meta carries facts about the sign-in, never a token:

      expires_at          when the access token stops working
      expired             it already has
      refresh_expires_at  when the sign-in itself ends: past this, Claude Code
                          cannot renew anything and only a new sign-in helps
      refresh_expired     it already has
      has_access          an access token is present at all
      can_renew           a refresh token is present and has not ended, so
                          Claude Code can still renew the access token
      missing             no Claude Code sign-in is saved on this Mac at all

    Claude Code empties both tokens when a renewal is refused (seen 2026-09-29
    at 4:59 AM, two hours after the sign-in ended), but leaves the entry and
    its refreshTokenExpiresAt in place. That state is "signed out", and before
    this it was reported as "no credential found", which sent people looking
    for a Keychain problem that did not exist.
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
        return None, None, {"missing": True, "can_renew": False,
                            "error": "No Claude Code sign-in is saved on this Mac (looked in "
                                     "the Keychain under %s, and in %s)."
                                     % (", ".join(KEYCHAIN_SERVICES), CRED_FILE)}

    return _meta_from(blob, source)


def _meta_from(blob, source, now_ms=None):
    """(token, source, meta) from a parsed credential. Split out so the rules
    about what counts as signed out can be tested without a Keychain."""
    oauth = blob.get("claudeAiOauth") or blob
    tok = oauth.get("accessToken")
    exp = _as_ms(oauth.get("expiresAt"))
    rexp = _as_ms(oauth.get("refreshTokenExpiresAt"))
    if now_ms is None:
        now_ms = datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000
    refresh_expired = rexp is not None and rexp < now_ms
    meta = {
        "expires_at": _ms_to_iso(exp),
        "expired": exp is not None and exp < now_ms,
        "refresh_expires_at": _ms_to_iso(rexp),
        "refresh_expired": refresh_expired,
        "has_access": bool(tok),
        "can_renew": bool(oauth.get("refreshToken")) and not refresh_expired,
    }
    if not tok:
        meta["error"] = ("Claude Code is signed out on this Mac, so UseMeUp cannot read your "
                         "limits." if not meta["can_renew"] else
                         "Claude Code's saved sign-in has no access token right now. Claude "
                         "Code renews it on its next call.")
        return None, source, meta
    return tok, source, meta


def signed_out(meta):
    """True when nothing but a new sign-in can bring the readings back: the
    access token is gone or expired, and the sign-in can no longer renew it."""
    if not meta or meta.get("missing") or "has_access" not in meta:
        return False                    # nothing was read, so nothing is known
    stuck = not meta.get("has_access") or meta.get("expired")
    return bool(stuck) and not meta.get("can_renew")


# How far ahead of the end of a sign-in the app starts saying so.
SIGNIN_WARN_SECONDS = 3 * 86400


def signin_summary(meta, now=None):
    """Where Claude Code's sign-in on this Mac stands. Never contains a token.

    state       ok          signed in, ending more than SIGNIN_WARN_SECONDS away
                expiring    signed in, ending within SIGNIN_WARN_SECONDS
                signed_out  saved, but only a new sign-in brings readings back
                missing     no Claude Code sign-in saved on this Mac at all
                unknown     signed in, but the credential states no end date
    ends_at     when the sign-in ends (ISO 8601, whole seconds, UTC). While it
                can renew, that is the sign-in's own end; once it cannot, it is
                the last access token's, because readings carry on until then.
    seconds_left  until ends_at, floored at zero
    signin_ends_at  the sign-in's own end (refreshTokenExpiresAt), or None. Unlike
                ends_at it does not move when the access token outlives the
                sign-in or Claude Code empties the tokens, so notification ids
                are built from it.
    """
    if not meta or ("has_access" not in meta and not meta.get("missing")):
        return None                     # no credential was read (status line source)
    if meta.get("missing"):
        return {"state": "missing", "ends_at": None, "seconds_left": None,
                "signin_ends_at": None}
    ends = meta.get("refresh_expires_at") if meta.get("can_renew") else (
        meta.get("expires_at") if meta.get("has_access") else None)
    if not ends and not meta.get("can_renew"):
        ends = meta.get("refresh_expires_at")      # when it ran out, for the record
    now = now or datetime.datetime.now(datetime.timezone.utc)
    left = None
    if ends:
        try:
            t = datetime.datetime.fromisoformat(ends)
            left = max(0, int((t - now).total_seconds()))
        except ValueError:
            left = None
    if signed_out(meta):
        state = "signed_out"
    elif left is None:
        state = "unknown"
    elif left <= SIGNIN_WARN_SECONDS:
        state = "expiring"
    else:
        state = "ok"
    return {"state": state, "ends_at": ends, "seconds_left": left,
            "signin_ends_at": meta.get("refresh_expires_at")}


# --- Keeping the token fresh without ever touching the refresh token -------------
# The access token lives 8-12 hours and only Claude Code refreshes it, on a real API
# call. Rather than use the refresh token ourselves (many OAuth servers rotate it on
# use, and mishandling that would break the CLI login), we ask the CLI to make one
# cheap call and then re-read the Keychain. This module stays read-only on the
# credential; Claude Code remains the only thing that ever writes it.
#
# It runs only while renewal is still possible (meta["can_renew"]). Once the
# sign-in itself has ended, `claude -p ok` cannot help: on 2026-09-29 it ran
# every ten minutes from 4:59 AM until 3:31 PM, 56 times, each one failing with
# "OAuth session expired and could not be refreshed" and each leaving a
# transcript behind. And when a renewal does fail while it should have worked
# (the network was down, say), the next try waits twice as long as the last,
# up to REFRESH_BACKOFF_CAP, instead of repeating every ten minutes forever.
#
# The command is found by claude_exe(), never by trusting PATH alone: a server
# started by the menu bar app or by launchd has PATH=/usr/bin:/bin:/usr/sbin:/sbin,
# and `claude` lives in ~/.local/bin.
REFRESH_ARGS = ["-p", "ok", "--model", "claude-haiku-4-5"]
REFRESH_CWD = config.REFRESH_CWD
REFRESH_COOLDOWN = 600      # never more than once every 10 minutes
REFRESH_BACKOFF_CAP = 4 * 3600
_refresh_state = {"last": 0.0, "fails": 0}


def claude_exe():
    """Full path of the `claude` command, or None (config.claude_exe)."""
    return config.claude_exe()


def renewal_state(auto_refresh=None):
    """Why an expired token is or is not being renewed: "off", "no_cli" or "on"."""
    if auto_refresh is None:
        auto_refresh = config.AUTO_REFRESH
    if not auto_refresh:
        return "off"
    return "on" if claude_exe() else "no_cli"


def refresh_wait():
    """Seconds that must pass after the last attempt before the next one."""
    return min(REFRESH_COOLDOWN * (2 ** _refresh_state["fails"]), REFRESH_BACKOFF_CAP)


def note_refresh(worked):
    """Record whether an attempt left a usable token, which sets the next wait."""
    _refresh_state["fails"] = 0 if worked else min(_refresh_state["fails"] + 1, 16)


def refresh_via_claude_code():
    """Trigger Claude Code's own OAuth refresh. Returns a status dict, never a token."""
    wait = refresh_wait()
    if time.time() - _refresh_state["last"] < wait:
        return {"ran": False, "reason": "waiting; the last attempt was under %d minutes ago"
                % (wait // 60)}
    _refresh_state["last"] = time.time()
    exe = claude_exe()
    if not exe:
        return {"ran": False, "reason": "the `claude` command was not found on PATH or in %s"
                % ", ".join(config.CLI_DIRS)}
    # Claude Code may shell out itself, so its own folder and the system
    # folders go on the PATH it gets.
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(p for p in [os.path.dirname(exe), env.get("PATH", ""),
                                              "/usr/bin:/bin:/usr/sbin:/sbin"] if p)
    try:
        os.makedirs(REFRESH_CWD, exist_ok=True)
        with open(os.devnull) as devnull:
            out = subprocess.run([exe] + REFRESH_ARGS, cwd=REFRESH_CWD, stdin=devnull,
                                 env=env, capture_output=True, text=True, timeout=120)
        return {"ran": True, "returncode": out.returncode,
                "detail": _safe(((out.stdout or "") + (out.stderr or "")).strip()[:200])}
    except FileNotFoundError:
        return {"ran": False, "reason": "the `claude` command at %s could not be started" % exe}
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


def probe(allow_ping=False, auto_refresh=None, source=None):
    """Rate-limit state from the configured source. Never contains a credential.

    endpoint    the usage endpoint only (the original behaviour, and the default)
    statusline  the status line drop file only; no Keychain read, no network
    auto        the endpoint, and the status line if the endpoint gives nothing.
                The endpoint goes first because only it has the model-scoped
                weekly window. A fallback result says so in `fallback_from`.
    """
    source = source or config.SOURCE
    if source == "statusline":
        return statusline.read()
    d = _probe_endpoint(allow_ping, auto_refresh)
    if source == "auto" and not d.get("ok"):
        alt = statusline.read()
        if alt.get("ok"):
            alt["fallback_from"] = {"reason": d.get("reason"), "error": d.get("error")}
            return alt
    return d


def _probe_endpoint(allow_ping=False, auto_refresh=None):
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
    if auto_refresh and (not token or meta.get("expired")) and meta.get("can_renew"):
        refresh = refresh_via_claude_code()
        if refresh.get("ran"):
            token, source, meta = _token()
            note_refresh(bool(token) and not meta.get("expired"))

    base = {"ok": False, "source": source, "windows": [], "raw": {},
            "token": {k: v for k, v in meta.items() if k != "error"},
            "signin": signin_summary(meta),
            "refresh": refresh}

    if signed_out(meta):
        base["reason"] = "signed_out"
        ended = _local_time(meta.get("refresh_expires_at"))
        base["error"] = ("Claude Code is signed out on this Mac, so UseMeUp cannot read your "
                         "limits." + (" Its sign-in ended %s." % ended if ended else ""))
        del token
        return base

    if not token:
        base["error"] = meta.get("error", "no usable credential")
        base["reason"] = "no_credential" if meta.get("missing") or not source else "token_expired"
        if base["reason"] == "token_expired":
            base["renewal"] = renewal_state(auto_refresh)
        return base

    if meta.get("expired"):
        base["reason"] = "token_expired"
        base["renewal"] = renewal_state(auto_refresh)
        why = (("Tried to renew it through Claude Code and it did not take: %s"
                % (refresh.get("detail") or refresh.get("reason")))
               if refresh else
               "Automatic renewal is off (set USEMEUP_AUTO_REFRESH=1 to turn it on).")
        base["error"] = ("The stored Claude Code access token expired %s. %s"
                         % (_local_time(meta.get("expires_at")) or "at an unknown time", why))
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

    if (result is None and status == 401 and auto_refresh and refresh is None
            and meta.get("can_renew", True)):
        refresh = refresh_via_claude_code()
        if refresh.get("ran"):
            tok2, source2, meta2 = _token()
            base["signin"] = signin_summary(meta2)
            if not (tok2 and not meta2.get("expired")):
                note_refresh(False)
            else:
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
                note_refresh(bool(windows))
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
            "error": ("The API rejected the stored token (HTTP 401). Claude Code renews it on "
                      "its next call." if auth_fail else
                      "Credential loaded from %s, but no endpoint returned usable limit data."
                      % source)})
        return base

    result.update({"ok": True, "source": source, "attempts": attempts,
                   "token": base["token"], "signin": base["signin"], "refresh": refresh,
                   "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat()})
    return result


if __name__ == "__main__":
    print(json.dumps(probe(), indent=2))
