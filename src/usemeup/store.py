#!/usr/bin/env python3
"""
store.py - incremental SQLite index of local Claude usage records.

Reads (read-only, never writes to either):
  ~/.claude/projects/**/*.jsonl                       -> source "cli"
  <Cowork local-agent-mode-sessions>/**/*.jsonl       -> source "cowork"
Writes: ~/.usemeup/usage.db  (its own database, nothing else)
Network: none.  Credentials: none.

Two things here are easy to get wrong and both were real bugs:

1. Deduplication. A streamed response is written many times as it grows, and a
   resumed session replays compacted copies. The identity is
   (requestId, message.id), but you must keep the row with the LARGEST token
   total, not the first one written, or you record a partial with
   output_tokens=1 in place of the finished 56,354.

2. Day bucketing. Days are cut in the user's own timezone. Bucketing by the UTC
   date moves roughly 10% of calls onto the wrong day for a US user.
"""
import json
import os
import sqlite3
import time

from . import config

CLI_ROOT = config.CLI_ROOT
COWORK_ROOT = config.COWORK_ROOT
DB_PATH = config.DB_PATH

SCHEMA_VERSION = "3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  path TEXT PRIMARY KEY, mtime REAL, size INTEGER, scanned_at REAL
);
CREATE TABLE IF NOT EXISTS calls (
  key TEXT PRIMARY KEY,
  ts TEXT, day TEXT, model TEXT, source TEXT, project TEXT,
  session TEXT, session_label TEXT, is_subagent INTEGER,
  input INTEGER, output INTEGER, thinking INTEGER,
  cache_read INTEGER, cache_write INTEGER,
  cache_5m INTEGER, cache_1h INTEGER, web_search INTEGER,
  total INTEGER
);
CREATE INDEX IF NOT EXISTS idx_calls_day ON calls(day);
CREATE INDEX IF NOT EXISTS idx_calls_ts ON calls(ts);
CREATE INDEX IF NOT EXISTS idx_calls_model ON calls(model);
CREATE INDEX IF NOT EXISTS idx_calls_source ON calls(source);
CREATE INDEX IF NOT EXISTS idx_calls_session ON calls(session);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS limit_samples (
  ts TEXT, key TEXT, used_pct REAL, resets_at TEXT,
  PRIMARY KEY (ts, key)
);
CREATE INDEX IF NOT EXISTS idx_samples_key_ts ON limit_samples(key, ts);
"""

COLUMNS = 18  # keep in sync with the calls table


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    ver = db.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()
    if not ver or ver["v"] != SCHEMA_VERSION:
        # Semantics or columns changed: drop and rebuild (~15s). CREATE TABLE
        # IF NOT EXISTS will not alter an existing table, so DELETE is not enough.
        # Note: limit_samples is deliberately NOT dropped. Those are timestamped
        # observations of the account's rate-limit state that cannot be recovered
        # by re-reading transcripts, so a schema rebuild must not discard them.
        db.execute("DROP TABLE IF EXISTS calls")
        db.execute("DROP TABLE IF EXISTS files")
        db.executescript(SCHEMA)
        db.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)",
                   (SCHEMA_VERSION,))
        db.commit()
    return db


def _pretty_project(slug):
    """Turn a project-folder slug into something a human recognises."""
    t = (slug or "").lstrip("-").replace("-", "/")
    if "scratch/workspaces" in t or "Application/Support/Claude" in t:
        return "desktop scratch"
    if "Users/" in t:
        after = t.split("Users/", 1)[1]
        parts = after.split("/", 1)
        return "~/" + parts[1] if len(parts) > 1 and parts[1] else "home directory"
    return t or "unknown"


def _identify(path):
    """(session_id, project_slug, is_subagent) from a transcript path.

    Layouts handled:
      .../projects/<slug>/<session>.jsonl
      .../projects/<slug>/<session>/subagents/agent-*.jsonl
    A subagent transcript is attributed to its parent session, so a session's
    cost includes the agents it spawned.
    """
    parent = os.path.basename(os.path.dirname(path))
    if parent == "subagents":
        session_dir = os.path.dirname(os.path.dirname(path))
        return (os.path.basename(session_dir),
                os.path.basename(os.path.dirname(session_dir)), 1)
    return (os.path.splitext(os.path.basename(path))[0], parent, 0)


def _walk():
    """Yield (path, source) for every transcript worth reading."""
    if os.path.isdir(CLI_ROOT):
        for dirpath, _dn, filenames in os.walk(CLI_ROOT):
            # Skip the throwaway sessions this tool makes to refresh the token.
            if config.excluded(dirpath):
                continue
            for fn in filenames:
                if fn.endswith(".jsonl"):
                    yield os.path.join(dirpath, fn), "cli"
    if COWORK_ROOT and os.path.isdir(COWORK_ROOT):
        for dirpath, _dn, filenames in os.walk(COWORK_ROOT):
            for fn in filenames:
                # audit.jsonl is an event log, not a transcript; it has no usage.
                if fn.endswith(".jsonl") and fn != "audit.jsonl":
                    yield os.path.join(dirpath, fn), "cowork"


def _records(path, source):
    session, slug, is_sub = _identify(path)
    project = "Cowork (desktop)" if source == "cowork" else _pretty_project(slug)
    label = _pretty_project(slug)
    try:
        fh = open(path, errors="replace")
    except OSError:
        return
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
            rid, mid = d.get("requestId"), m.get("id")
            key = ("%s|%s" % (rid or "", mid or "")) if (rid or mid) else \
                  ("ts:%s|%s|%s" % (d["timestamp"], m.get("model"), u.get("output_tokens")))
            cc = u.get("cache_creation") or {}
            std = u.get("output_tokens_details") or {}
            stu = u.get("server_tool_use") or {}
            inp = u.get("input_tokens") or 0
            out = u.get("output_tokens") or 0
            cr = u.get("cache_read_input_tokens") or 0
            cw = u.get("cache_creation_input_tokens") or 0
            yield (key, d["timestamp"], config.local_day(d["timestamp"]),
                   m.get("model") or "unknown", source, project,
                   session, label, is_sub,
                   inp, out, std.get("thinking_tokens") or 0,
                   cr, cw,
                   cc.get("ephemeral_5m_input_tokens") or 0,
                   cc.get("ephemeral_1h_input_tokens") or 0,
                   stu.get("web_search_requests") or 0,
                   inp + out + cr + cw)


UPSERT = """INSERT INTO calls VALUES (%s)
            ON CONFLICT(key) DO UPDATE SET
              ts=excluded.ts, day=excluded.day, model=excluded.model,
              source=excluded.source, project=excluded.project,
              session=excluded.session, session_label=excluded.session_label,
              is_subagent=excluded.is_subagent,
              input=excluded.input, output=excluded.output, thinking=excluded.thinking,
              cache_read=excluded.cache_read, cache_write=excluded.cache_write,
              cache_5m=excluded.cache_5m, cache_1h=excluded.cache_1h,
              web_search=excluded.web_search, total=excluded.total
            WHERE excluded.total > calls.total""" % ",".join("?" * COLUMNS)


def record_limit_sample(windows):
    """Persist one observation per rate-limit window, for burn-rate projection.

    A single utilization reading only supports an average-pace guess. Keeping a
    short history lets the projection use the recent slope instead, which is what
    you actually want when a quiet week ends in a heavy session.
    """
    if not windows:
        return 0
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    rows = [(now, w.get("key"), float(w.get("used_pct") or 0), w.get("resets_at"))
            for w in windows if w.get("key") is not None]
    if not rows:
        return 0
    db = connect()
    db.executemany("INSERT OR REPLACE INTO limit_samples VALUES (?,?,?,?)", rows)
    # Keep it small; anything older than 30 days cannot inform a 7-day window.
    cutoff = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=30)).isoformat()
    db.execute("DELETE FROM limit_samples WHERE ts < ?", (cutoff,))
    db.commit()
    db.close()
    return len(rows)


def limit_samples(key, since_iso):
    db = connect()
    rows = [dict(r) for r in db.execute(
        "SELECT ts, used_pct, resets_at FROM limit_samples"
        " WHERE key=? AND ts>=? ORDER BY ts", (key, since_iso))]
    db.close()
    return rows


def ingest(progress=None):
    """Scan both roots, parsing only files that changed since last time."""
    db = connect()
    known = {r["path"]: (r["mtime"], r["size"]) for r in db.execute("SELECT * FROM files")}
    todo, skipped = [], 0
    for path, source in _walk():
        try:
            st = os.stat(path)
        except OSError:
            continue
        if known.get(path) == (st.st_mtime, st.st_size):
            skipped += 1
            continue
        todo.append((path, source, st.st_mtime, st.st_size))

    t0, added, done = time.time(), 0, 0
    for path, source, mtime, size in todo:
        batch = list(_records(path, source))
        if batch:
            cur = db.executemany(UPSERT, batch)
            added += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?)",
                   (path, mtime, size, time.time()))
        done += 1
        if progress and (done % 250 == 0 or done == len(todo)):
            progress(done, len(todo), added)
    db.commit()
    total = db.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
    db.close()
    return {"files_scanned": len(todo), "files_unchanged": skipped,
            "new_calls": added, "total_calls": total,
            "seconds": round(time.time() - t0, 1)}


if __name__ == "__main__":
    def show(done, total, added):
        print("  %d/%d files, %d rows written" % (done, total, added), flush=True)
    print(json.dumps(ingest(show), indent=2))
