# UseMeUp

A local dashboard for your Claude usage: live rate-limit windows, token and cost
totals, and which sessions actually cost you something.

It reads transcripts already sitting on your disk. No account to create, no data
leaves your machine, and the only network call it ever makes is to
`api.anthropic.com` to ask what your rate-limit windows look like.

```
python3 server.py
```

Python 3.9+, standard library only. No pip install, no build step, no daemon.

---

## It can prove its own numbers

Every tool in this category claims the numbers are right. This one ships the check.

```
python3 verify.py
```

`verify.py` re-reads your raw transcripts and recounts everything from scratch. It
shares no counting, deduplication or pricing code with the dashboard, so it cannot
inherit the dashboard's mistakes. It compares every day and exits non-zero if
anything disagrees.

```
recounting from raw transcripts (importing none of the tool's logic)…
  3,448 files read, 104,222 unique calls after dedup, 164 days

checked 164 day(s)
  recount total : $22486.88
  index total   : $22486.88

OK. Every checked day matches on calls, output tokens and cost.
```

This is not decoration. It found three real bugs during development, each of which
had already survived a round of "I checked the code and it looks right":

1. **Deduplication kept the wrong copy.** A streamed response is written to the
   transcript repeatedly as it grows, and resumed sessions replay compacted copies.
   Keeping the first record meant storing `output_tokens: 1` where the finished
   response was `56,354`. Fixed by keeping the row with the largest token total.
2. **Days were bucketed by UTC date.** About 10% of calls landed on the wrong
   calendar day for a US user; an evening session bled into tomorrow. Fixed by
   cutting days in your own timezone.
3. **The recount and the index disagreed about what to exclude.** The tool can make
   its own throwaway calls to refresh a token, and only one of the two was ignoring
   them. Fixed by defining the rule once, in `config.py`, and having both read it.

The first two were found by recounting, not by reading code. That is the entire
argument for shipping the recount.

---

## What you get

**Rate-limit windows, live.** Your 5-hour session window and 7-day windows, each
drawn as its real calendar days with a marker showing where the clock actually is.
The gap between the marker and the fill is a pace read: marker well ahead means the
week's capacity will expire unused.

**Burn-rate projection.** Where each window is heading at your current rate, and
when it will hit 100% if it will. It says which basis it used: `recent` when there
is enough sampled history to measure a slope, `average` when there is only one
reading. It is a straight-line extrapolation and is labelled as one.

**Most expensive sessions.** Ranked by cost, with subagent calls folded into the
session that spawned them. This is usually the fastest way to find where the money
went.

**Spend by day, model, project and source**, with the day chart's model colouring
following the visible date range so a model you adopted last week leads the chart
immediately instead of hiding behind months of older usage.

---

## Where the data comes from

| Source | Location |
|---|---|
| Claude Code CLI | `~/.claude/projects/**/*.jsonl` |
| Cowork desktop | `~/Library/Application Support/Claude/local-agent-mode-sessions/**/*.jsonl` |

Cowork is typically far the larger of the two. It is stored per session rather than
per project, so it appears as one bar in the project chart.

**Cloud sessions are in neither.** They run on Anthropic's servers and write nothing
to your machine. They are counted in the rate-limit meters, which are account-wide,
and cannot appear in the charts, which are built from local files. That gap is real
and the UI says so rather than quietly implying the charts are the whole picture.

The Cowork path is macOS-specific. On Linux and Windows the tool looks in the
platform equivalents and, finding nothing, says so at startup and shows CLI data
only rather than reporting a silently partial total.

---

## What each file touches

| File | Reads | Network | Credential |
|---|---|---|---|
| `config.py` | nothing | none | none |
| `store.py` | your transcripts (read-only) → `~/.usemeup/usage.db` | none | none |
| `parse_usage.py` | `~/.usemeup/usage.db` | none | none |
| `burn.py` | recorded samples | none | none |
| `verify.py` | your transcripts, the index | none | none |
| `rate_limits.py` | macOS Keychain, or `~/.claude/.credentials.json` | api.anthropic.com only | **yes**, in memory |
| `server.py` | the above | binds `127.0.0.1` only | none |

`rate_limits.py` is the only file worth auditing closely. The token is never
written to disk, logged, cached, or returned; everything the module returns passes
through `_safe()` first. It is **read-only** on your credential: it never writes to
the Keychain and never uses the refresh token, so it cannot rotate or invalidate
your Claude Code login.

On first run macOS asks whether Python may read the Claude Code credential. Deny it
and everything except the rate-limit panel still works.

---

## Two things to know before you run it

**Screenshots leak your directory names.** The project and session charts render the
paths you have worked in. Before you post a screenshot anywhere:

```
USEMEUP_DEMO=1 python3 server.py
```

Demo mode replaces project and session labels with stable pseudonyms and keeps every
number real, so the page stays coherent and is safe to share.

**Auto-refresh spends your tokens, so it is off by default.** The Claude Code access
token lives 8 to 12 hours and only Claude Code refreshes it, on a real API call. If
you have not used the CLI recently the token is stale and the rate-limit panel cannot
load. With `USEMEUP_AUTO_REFRESH=1` the dashboard will run `claude -p ok` itself to
make Claude Code refresh its own credential, at a cooldown of once per ten minutes.
That costs a handful of Haiku tokens roughly once a day. It is opt-in because a tool
that quietly makes billable calls on your account should not be a surprise you
discover later.

Without it, the panel tells you the token expired and that `claude -p ok` fixes it.
(`claude auth status` does **not** refresh it; it only reads local state.)

---

## Configuration

| Variable | Effect |
|---|---|
| `USEMEUP_PORT` | server port (default 8787) |
| `USEMEUP_TZ` | IANA zone for day bucketing (default: your system zone) |
| `USEMEUP_DEMO=1` | redact project and session names |
| `USEMEUP_AUTO_REFRESH=1` | allow the token refresh described above |
| `USEMEUP_DB` | override the index location |

---

## Notional spend

Dollar figures apply published API list prices from `pricing.json` to your token
counts. **A Claude subscription is not billed per token**, so this is a relative size
signal, not an invoice. It answers "which work is expensive" well and "what do I owe"
not at all.

Cache reads dominate token volume and price at a tenth of input, so long agentic
sessions with large replayed context dominate the totals. A short, high-value
conversation will always look small next to one long agentic run.

When a new model appears, add it to `pricing.json`. Until you do it shows as
`unpriced` rather than silently counting as zero. Prices from
[the Claude pricing page](https://platform.claude.com/docs/en/about-claude/pricing).

---

## How it stays fast

About 100k call records across roughly 5 GB of transcripts. A `PRIMARY KEY` on
`(requestId, message.id)` makes deduplication exact, and files whose mtime and size
have not changed are skipped, so a refresh costs a `stat()` per file. First ingest is
about 15 seconds; after that it is effectively free. `GET /api/ingest` forces a
rescan, `GET /api/export` dumps everything as JSON.

`store.py` carries a `SCHEMA_VERSION`. Bumping it drops and rebuilds the index on the
next connect, which is how column or semantics changes take effect. Recorded
rate-limit samples deliberately survive that rebuild, since they are timestamped
observations that cannot be recovered by re-reading transcripts.

---

## License

MIT. See [LICENSE](LICENSE).
