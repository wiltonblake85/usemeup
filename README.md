# UseMeUp

A local dashboard for your Claude usage: live rate-limit windows, token and cost
totals, and which sessions actually cost you something.

It reads transcripts already sitting on your disk. No account to create, no data
leaves your machine, and the only network call it ever makes is to
`api.anthropic.com` to ask what your rate-limit windows look like.

```
uvx --from git+https://github.com/wiltonblake85/usemeup usemeup
```

or, if you prefer it installed:

```
pipx install git+https://github.com/wiltonblake85/usemeup
usemeup
```

Python 3.9+, **no dependencies at all** — standard library only, so the entire
supply chain is this one repo.

```
usemeup            index transcripts and open the dashboard
usemeup verify     recount from raw transcripts and check the index
usemeup daily      a terminal table, no browser needed
usemeup prices     show the resolved price table and where it came from
usemeup ingest     re-scan without serving
usemeup agent      install | status | uninstall a macOS LaunchAgent that keeps it running
```

From a clone: `PYTHONPATH=src python3 -m usemeup.cli`. Unit tests, also stdlib
only: `PYTHONPATH=src python3 -m unittest discover tests`.

---

## It can prove its own numbers

Every tool in this category claims the numbers are right. This one ships the check.

```
usemeup verify
```

`usemeup verify` re-reads your raw transcripts and recounts everything from scratch. It
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
4. **The exclusion rule was a substring.** It skipped any path containing
   `usemeup`, which would have silently dropped every Claude Code session run inside
   a checkout of this repo. Found in an audit before it bit anyone; the rule is now
   an exact match on the refresh folder's name, applied to both transcript roots.

The first two were found by recounting, not by reading code. That is the entire
argument for shipping the recount.

Two definitions worth knowing when you check a single day by hand:

- A call is dated by the timestamp of its largest recorded copy. For a streamed
  response that is the final chunk, so a call that starts at 11:59 PM and finishes
  at 12:01 AM belongs to the second day, in the index and in the recount alike.
- Claude Code writes placeholder assistant messages with the model name
  `<synthetic>` (a request that failed before a response arrived, for instance).
  They carry a usage block of zeros and are not API calls, so neither side counts
  them.

---

## What you get

**Rate-limit windows, live.** Your 5-hour session window and 7-day windows, read
straight from the API, with the reset countdown and a pace verdict for each.

**The week as seven daily buckets.** Anthropic gives you one number for the week.
Most people plan in days, so each 7-day window is also drawn as seven bars: one
seventh of the week (14.3%) is the day's share, a bar shows what that day actually
consumed, and the part above the share line is the overspend, in red, with the
amount on it. Unspent share banks forward, so a quiet Sunday means Monday can spend
more; a heavy Tuesday draws the bank down. Today's bar carries a dashed cap at what
it may still spend (its share plus the bank), and the line under the chart states
it plainly: *11.5% of a 14.3% share, +46.9% banked from earlier days, 49.7% left
today*.

Days are cut at the hour the week resets, not at midnight, so the seven bars sum
exactly to the weekly figure and the last one ends at reset. Up to four prior
weeks are summarised under the chart as history accumulates.

Two honesty rules, because this is derived, not reported. First, the API reports
whole percent, so a day under 1% of the week reads as 0. Second, daily consumption
is the rise in the cumulative figure while something was sampling it. The server
samples every five minutes while it runs (see below). A rise that happened while
it was not running is still real usage, but its timing is not known. It is spread
evenly across the gap, drawn hatched, and bracketed on the chart with the amount:
*24.5% over 5.4 days not sampled, timing unknown*. The bank arithmetic assumes the
even spread and the caption says so.

Evenly, and not by some cleverer proxy, on purpose. An earlier build placed gap
usage by the token volume in the local transcript index. Cloud sessions leave no
transcripts on this machine, so that proxy was blind to exactly the work it was
meant to locate, and it drew four near-empty days that were in fact busy. A method
that cannot see half the usage should not pretend to know where it went.

**Burn-rate projection, as a chart.** Each window gets a burn-up chart: a solid
line for what you have actually consumed, a dashed line for where the current rate
lands you by reset, and a grey diagonal for even pace. Sitting below the diagonal
means the window will expire with capacity unused; crossing it means you are
outrunning the clock, and the point where the projection meets 100% is marked.

The projection says which basis it used: `recent` when enough sampled history
exists to measure a slope, `average` when there is only one reading. It is a
straight-line extrapolation and is labelled as one.

**Most expensive sessions.** Ranked by cost, with subagent calls folded into the
session that spawned them. This is usually the fastest way to find where the money
went.

**Spend by day, model, project and source.** One colour per model, fixed: Opus is
persimmon, Fable is green, Sonnet is blue, Haiku is purple, and within a family the
newest generation is drawn at full strength with each earlier one a step paler.
A model keeps its colour across the 7d/30d/90d ranges and from one day to the
next, so you can learn the chart once.

**What this Mac cannot see, on the face of the chart.** The spend charts are built
from transcripts on this machine. Cloud sessions write nothing here (see below), so
a day of cloud work is an empty bar. The account-wide rate-limit windows, sampled
every five minutes while the server runs, know better: each day's tooltip shows how
far each weekly window moved that day, and a ring above a bar marks a day where a
window scoped to a model climbed while the local index holds no calls for that
model. That ring is the difference between "nothing happened Tuesday" and "Tuesday
happened somewhere else".

---

## Where the data comes from

| Source | Location |
|---|---|
| Claude Code CLI | `~/.claude/projects/**/*.jsonl` |
| Cowork desktop | `~/Library/Application Support/Claude/local-agent-mode-sessions/**/*.jsonl` |

The Cowork root holds only what the desktop app executed **on this machine**. It is
stored per session rather than per project, so it appears as one bar in the project
chart, labelled "Cowork, local runs". If you mostly work in cloud sessions, that bar
is largely your local scheduled tasks, and the label is chosen so it does not read as
"all of Cowork".

**Cloud sessions are in neither.** Cowork in the cloud, Claude Code on the web and
cloud scheduled tasks run on Anthropic's servers and write nothing to your machine.
They are counted in the rate-limit meters, which are account-wide, and cannot appear
in the charts, which are built from local files. That gap is real, and the day chart
marks it per day (see above) rather than quietly implying the bars are the whole
picture.

The two halves of the page also cut days differently, by design: the allotment
view cuts at the weekly reset hour so seven bars sum to the weekly figure; the spend
charts cut at midnight in your zone because that is what a calendar day means. Each
chart says which it uses.

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
| `pricing.py` | the cache, the bundled table | **raw.githubusercontent.com** (public file, GET) | none |
| `burn.py` | recorded samples | none | none |
| `daily.py` | recorded samples | none | none |
| `coverage.py` | recorded samples | none | none |
| `agent.py` | writes `~/Library/LaunchAgents/…usemeup.plist`, runs `launchctl` | none | none |
| `verify.py` | your transcripts, the index | none | none |
| `rate_limits.py` | macOS Keychain, or `~/.claude/.credentials.json` | api.anthropic.com only | **yes**, in memory |
| `server.py` | the above | binds `127.0.0.1` only | none |

While it runs, `server.py` also probes the rate-limit endpoint on its own every five
minutes, so the daily buckets keep filling with the page closed. It goes through the
same cache and back-off as the page does, so that is the ceiling either way: one
probe per five minutes, never more, and the 429 back-off applies to both. Samples
are kept for 90 days in `~/.usemeup/usage.db`, a few megabytes at most.

Two files reach the network and no others. `pricing.py` does a plain GET of a
public price list; nothing about you is sent, no token, no identifiers, no query
parameters, and `USEMEUP_OFFLINE=1` disables it. `rate_limits.py` is the one worth
auditing closely. The token is never
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

## Keeping it running (macOS)

While it runs, the server also re-scans the transcript folders on the same five-minute tick (unchanged files are skipped), so the charts are current the moment the page opens.

The daily view is only as complete as the hours the server was up to sample. A
Terminal window you close at night is a gap in tomorrow's chart. So on macOS:

```
usemeup agent install
```

writes a LaunchAgent (`~/Library/LaunchAgents/com.wiltonblake.usemeup.plist`) that
starts the dashboard at login, restarts it if it exits, and keeps it serving with no
window open. It runs exactly what you would type, from the same Python and the same
install you ran the command from, so a source checkout has to stay where it is. Logs
go to `~/.usemeup/agent.log`; `usemeup agent status` shows whether it is loaded and
listening, and `usemeup agent uninstall` removes it.

The agent turns **auto-refresh on** by default, because an unattended meter that
goes stale every 8 to 12 hours defeats the purpose; pass `--no-auto-refresh` to keep
the read-only behaviour. The first time it runs, macOS may ask whether Python may
use the Claude Code credential in the Keychain. That prompt is the Keychain's, not
this tool's, and it appears once.

`ps` will show `python3 -m usemeup.cli serve --no-open` owned by launchd. It binds
`127.0.0.1` like every other run; the agent changes when it runs, not what it does.

---

## Configuration

| Variable | Effect |
|---|---|
| `USEMEUP_PORT` | server port (default 8787) |
| `USEMEUP_TZ` | IANA zone for day bucketing (default: your system zone) |
| `USEMEUP_DEMO=1` | redact project and session names |
| `USEMEUP_AUTO_REFRESH=1` | allow the token refresh described above |
| `USEMEUP_OFFLINE=1` | never fetch live prices; use the cache or bundled table |
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

Prices resolve from the community-maintained
[LiteLLM price database](https://github.com/BerriAI/litellm), cached locally for 24
hours, falling back to the bundled `pricing.json` when the network is unavailable.
`usemeup prices` shows the table and its provenance; the dashboard footer names the
source and its age, so a stale table is visible rather than quietly wrong.

Only rows whose `litellm_provider` is `anthropic` are used. The `us.anthropic.*`
rows in that file are Amazon Bedrock regional prices carrying a 10% premium, and
using them would overstate every figure by that much. The first-party rows were
cross-checked against
[Anthropic's published pricing](https://platform.claude.com/docs/en/about-claude/pricing)
for Opus 5, Opus 4.8, Opus 4.7, Fable 5.1, Sonnet 5 and Haiku 4.5, and matched
exactly on all five price fields.

`USEMEUP_OFFLINE=1` skips the fetch entirely.

---

## How it stays fast

About 100k call records across roughly 5 GB of transcripts. A `PRIMARY KEY` on
`(requestId, message.id)` makes deduplication exact, and files whose mtime and size
have not changed are skipped, so a refresh costs a `stat()` per file. First ingest is
about 15 seconds; after that it is effectively free. `GET /api/export` dumps everything as JSON.

`usemeup ingest` forces a rescan. `store.py` carries a `SCHEMA_VERSION`. Bumping it drops and rebuilds the index on the
next connect, which is how column or semantics changes take effect. Recorded
rate-limit samples deliberately survive that rebuild, since they are timestamped
observations that cannot be recovered by re-reading transcripts.

---

## License

MIT. See [LICENSE](LICENSE).
