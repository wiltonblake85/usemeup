# UseMeUp

Will your Claude subscription last until the reset? UseMeUp answers that for each
rate-limit window, on a local page and in the macOS menu bar, and it measures
your pace against the hours you actually work instead of a 24-hour clock.

Everything runs on your machine. By default it reads the limits Claude Code
already hands its status line: no credential, no network call. An opt-in mode
asks `api.anthropic.com` directly, which costs a Keychain read and buys the
per-model weekly window.

```
uvx --from git+https://github.com/wiltonblake85/usemeup usemeup
```

or, installed:

```
pipx install git+https://github.com/wiltonblake85/usemeup
usemeup
```

Python 3.9 or later and **no dependencies**. Standard library only, so the whole
supply chain is this repo.

```
usemeup              serve the page on http://127.0.0.1:8787
usemeup agent        install | status | uninstall a LaunchAgent that keeps it running
usemeup statusline   a Claude Code status line command that records your limits
usemeup ingest       re-scan local transcripts without serving
usemeup verify       recount the transcript index from raw files
usemeup daily        transcript totals as a terminal table
usemeup prices       the price table behind those totals, and where it came from
```

From a clone: `PYTHONPATH=src python3 -m usemeup.cli`. Tests: `python3 -m pytest tests`
runs all of them. `PYTHONPATH=src python3 -m unittest discover tests` needs
nothing installed but skips `test_panel.py` and `test_history.py`, which are
written as plain functions.

## Why another usage meter

There are plenty of menu bar meters for Claude limits. Most stop at a percentage
and a countdown, and the ones I found that forecast all do it the same way:
percent used, divided by the fraction of the window that has elapsed. That
treats a 7-day window as 168 equal hours. Nobody spends anything while asleep, so by Monday breakfast that arithmetic says you're
comfortably under pace, and by Wednesday afternoon it says you'll blow through
the limit on Sunday night. Both readings come from the clock, not from you.

On my own account, switching the basis from the clock to my working hours moved
one weekly forecast from "hits 100% Sunday 7:38 PM" to "does not hit 100%". The
first is an alarm you learn to ignore. The second is something you can plan a
week around.

## What the page shows

One card, **Rate limit windows**, with a block for each window the API reports:
the 5-hour session window, the 7-day window across all models, and any 7-day
window scoped to one model (on my plan that is "Fable only", and it is usually
the one that binds).

Each block leads with where the window ends up, in words: *landing near 92% at
reset*, or *runs out around Tuesday 9:25 AM*. Under that sit the pace verdict
with its burn rate, then the reset countdown.

### The week as seven daily buckets

Anthropic gives you one number for the week
and most people plan in days, so each 7-day window is also drawn as seven bars.
A seventh of the week (14.3%) is the day's share. A bar shows what that day
consumed, and anything above the share line is drawn in red with the amount on
it. Unspent share banks forward, so a quiet Sunday lets Monday spend more, and a
heavy Tuesday draws the bank down. Today's bar carries a dashed cap at what it
may still spend, and the caption says it outright: *11.5% of a 14.3% share,
+46.9% banked from earlier days, 49.7% left today*.

Days are cut at the hour the week resets, not at midnight, which is why the seven
bars sum exactly to the weekly figure. Up to four earlier weeks are summarised
under the chart as history builds.

### A burn-up chart per window

A solid line for what you've consumed, a dashed
line for where the forecast lands by reset, and a grey reference for even pace.
The point where the forecast meets 100% is marked with its day and time.

## How the forecast works

A two-button control on the card switches the basis between **24h pace** and
**work day**, with an hours-per-day selector that defaults to 10. On the
work-day basis the grey reference becomes a staircase that goes flat outside
working hours, the dashed forecast bends with it, and the verdict underneath is
recomputed to match, with the burn rate quoted per working hour. Windows shorter
than 36 hours stay on the wall clock, because a 5-hour window would otherwise use
up its "working day" before it closes.

### Your hours come from your own history, from two instruments

Hour of day
comes from the rate-limit samples the server records every five minutes: the rise
between two readings is consumption that happened in that interval, and because
the figure is account-wide it sees cloud sessions and claude.ai. The weekday
split comes from the local transcript index until enough weeks of samples pile up
to carry it, since one week of samples says things like "Sunday is 2% of the
week", which is noise. The page footnote always names which source drove the
hours. On my account the two instruments share no wiring and still land on the
same working day, 7 AM to about 4 PM.

### Early in a window, the forecast leans on past windows

Nine hours after a
reset there is almost no slope to measure. An earlier build handled that badly in
both directions: it either projected a flat line (you finish where you stand) or
extrapolated 21% in a morning to thousands of percent by the weekend. Now the
slope is a blend. Below 5% of the working time elapsed it comes from what a full
window has cost this account before; above 25% it comes from this window alone;
in between, from both in proportion. With no history and no span, the headline
reads *not enough history to project yet*. A number with nothing behind it is
the failure this replaced, so "no opinion" survives all the way to the screen,
and the payload carries a `source` field (history, blend, observed or clock) that
the page prints.

Those historical figures are floors. A window that reaches 100 stops rising, so
demand past the cap is invisible, and the code reports how many windows were
capped so that stays checkable.

### A spent window gets no pace verdict

At 100% used the verdict is *spent* and
the burn rate and forecast are dropped. Pace is a claim about the future, and a
window with no future needs different words. Before this rule, a window at 100%
used with 98% elapsed was cheerfully reported as "on pace".

### Colour means three things

Green is on pace or under. Amber is ahead of pace.
Red is the limit itself: spent, at 95% or above, or forecast to cross 100% before
reset. Red uses the forecast, not the raw number, because 92% used with two hours
left never touches the wall and 92% with three days left certainly will.

### Two honesty rules for the daily buckets

The buckets are derived, not reported, so two caveats apply. The API reports
whole percent. A day under 1% of the week therefore reads as zero. And daily
consumption is only the rise in the cumulative figure while something was there
to sample it, which means the server has to be running for a day to be measured. A rise that happened while nothing was sampling is still real
usage, but its timing is unknown, so it is spread evenly across the gap, drawn
hatched, and bracketed with the amount: *24.5% over 5.4 days not sampled, timing
unknown*. Evenly, and not by a cleverer proxy, on purpose. An earlier build
placed gap usage by local transcript volume. Cloud sessions leave no transcripts
here, so the proxy was blind to the work it was meant to locate and drew four
near-empty days that had been busy.

## The menu bar app (macOS)

`macapp/` is a small SwiftUI front end over the same local server. The bar shows
every weekly window side by side, the model-scoped one first:

```
[F 69%] [All 60%]
```

Each window is a small filled pill: green while it is fine, amber when you are
ahead of pace, red when the limit itself is in play. The first version drew grey
labels and coloured figures straight onto the bar, and I couldn't read it. The
menu bar takes its tint from the wallpaper behind it, so text drawn onto it has
no contrast you can count on. A pill carries its own background, with white text
on green and red and near-black on amber, and it reads the same over anything.

A window at its cap reads `spent`. The 5-hour window joins the others only while
it is amber or red: most days it is noise, but when it hits the wall it blocks
every model.

An earlier build let the single worst window take over the bar. That hid the
wrong thing. Once Fable is nearly spent I stop using Fable, that question's
closed, and the all-models figure is the one I need next. It shouldn't be
pushed out of view by a window I have already acted on.

### One notification per window, not a stream of them

The app tells you once when a window first turns red: the forecast says it will
hit 100% before it resets, or it has passed 95%. It tells you once more if the
window is spent. After that it stays quiet about that window however much the
forecast wobbles, because the server gives each alert an id made of the window
and the kind, and the app remembers the ids it has shown. An alert raised while
the app was closed still appears when it opens.

Two forecasts are deliberately not worth a banner. One is a forecast drawn only
from past weeks: my account usually runs out, so that rule would fire the moment
every window opened, which is a calendar reminder and not news. The other is a
forecast made in the first tenth of a window, where ten minutes at 5% reads as
150%. Passing 95% is a measurement, so neither exception applies to it.

macOS asks for permission the first time there is something to say, not at
launch. Settings has the switch, the current permission state, and a button
that sends a test.

Clicking the bar opens a panel with the same headline, verdict and reset
countdown per window as the page.

```
./macapp/build.sh              build, install to /Applications, start at login
./macapp/build.sh --swift      recompile the Swift only
./macapp/build.sh --no-install leave it in macapp/build/
./macapp/release.sh            Developer ID, notarized, stapled, in a notarized DMG
```

The build bundles the Python server with PyInstaller, so the app runs on a Mac
with no Python setup. At launch the app asks port 8787 who is there
(`/api/ping`). A UseMeUp server: it joins it. Some other program: it says so
rather than fight for the port. Nobody, but `usemeup agent install` has set up
the LaunchAgent: it waits up to 90 seconds for that agent, which starts at the
same login. Otherwise it starts its bundled server, and stops it again on quit.
If the server it was reading goes away, it goes through the same steps again.

Which source the bundled server reads, and whether it may renew an expired
sign-in, are chosen in the app's Settings and passed to the server explicitly.
They apply only to a server the app starts; a server it joined keeps its own
settings, and Settings says which are in force.
Building needs the Xcode command line tools and a Homebrew `python3.12`.

`build.sh` signs ad-hoc, which is enough on the Mac that built it. `release.sh`
is for handing it to anyone else: it re-signs every binary inside the bundle
with a Developer ID and the hardened runtime (PyInstaller leaves about sixty of
them, and notarization rejects the bundle if any one is unsigned), notarizes
and staples the app, then does the same for the DMG. The result opens on a
fresh Mac with no Gatekeeper warning. It is Apple silicon only, because the
bundled server is built from an arm64 Python.

## Advice, when a window needs it

A red or amber window also gets one sentence on what change lands it at exactly
100% by reset: *To last until reset, keep it under 4.1% a working day (the
current pace is 11%).* The budget is the headroom left, spread over the working
hours left before the reset, and the current pace is quoted on the same basis
so you can see how big the cut is. A model-scoped window adds that the rest can
go to another model, and once it is spent it says so outright, with the catch:
that work still counts against all models. A green window gets no advice, and
neither does an amber one whose arithmetic already lands under 100.

It appears on the page, in the menu bar panel, and in the notification body.

## The status file

`~/.usemeup/status.json` holds the whole picture for anything that would rather
open a file than call the server: every window with its headline, advice, verdict
and chart series, plus the current alerts. The server rewrites it every five
minutes and once at start, atomically, so a reader never sees half a file.

It also says how long it can be trusted. `checked_at` is when the figures were
last true and `fresh_for_seconds` (45 minutes) is how old that may get before a
reader should stop showing them. The rule lives in the file so every reader goes
blank at the same moment instead of each inventing its own. `status.is_fresh()`
is the reference implementation.

Transom, my notch app, reads this file for its limits section.

## Where the numbers come from

There are two sources, chosen with `USEMEUP_SOURCE`. The default touches no
credential at all.

### `statusline`, the default

Claude Code hands its status line command a JSON document that, for Pro and Max
subscribers, includes the 5-hour and 7-day figures
([docs](https://code.claude.com/docs/en/statusline)). `usemeup statusline` is
such a command. It saves those figures to `~/.usemeup/statusline.json` and prints
`5h 24% · 7d 41%`. The server reads that file, and `rate_limits.py` never opens
the Keychain or the network. Add one line to `~/.claude/settings.json`:

```json
{ "statusLine": { "type": "command", "command": "usemeup statusline" } }
```

With the menu bar app and no Python install, the command is the one bundled
inside the app:

```json
{ "statusLine": { "type": "command",
  "command": "/Applications/UseMeUp.app/Contents/Resources/server/usemeup-server statusline" } }
```

Until a reading arrives, the page says which of the two to use, since it knows
how it was installed.

### `endpoint`, opt-in

With `USEMEUP_SOURCE=endpoint`, `rate_limits.py` reads your Claude Code OAuth
token from the macOS Keychain (or `~/.claude/.credentials.json`), sends it to
`https://api.anthropic.com/api/oauth/usage` and nowhere else, keeps the limit
figures, and discards the token. This is the only source that reports the
model-scoped weekly window, which is why I run it on my own Mac. It is not the
default because a tool you downloaded should not read your credential unless you
tell it to.

Already have a status line? `usemeup statusline --passthrough "your command"`
runs yours on the same input and appends the figures.

I checked the two sources against each other on a live turn. The status line's
`five_hour` and `seven_day` matched the endpoint's session and all-models windows
on both percent and reset time, so they feed one history. What the status line
costs you:

- **No model-scoped weekly window.** In that same reading the all-models window
  stood at 60% and the Fable-only window at 69%. The status line couldn't see
  the second one, and it was the one that mattered.
- **Only as fresh as your last Claude Code turn on this machine.** Cloud sessions
  and claude.ai move the real number without moving this one. Each sample is
  filed under the time it was captured, never under "now", and a reading more
  than six hours old is refused instead of being shown as current.

`auto` uses the endpoint and falls back to the status line when the endpoint
gives nothing, so it reads the credential too. The response says so in `fallback_from`.

## What each file touches

| File | Reads | Network | Credential |
|---|---|---|---|
| `config.py` | nothing | none | none |
| `rate_limits.py` | macOS Keychain, or `~/.claude/.credentials.json` | api.anthropic.com only | **yes**, in memory |
| `statusline.py` | stdin from Claude Code, `~/.usemeup/statusline.json` | none | none |
| `history.py`, `burn.py`, `daily.py`, `coverage.py` | recorded samples | none | none |
| `panel.py` | the samples and the transcript index | none | none |
| `store.py` | your transcripts (read-only), writes `~/.usemeup/usage.db` | none | none |
| `parse_usage.py` | `~/.usemeup/usage.db` | none | none |
| `pricing.py` | the cache, the bundled table | raw.githubusercontent.com (public file, GET) | none |
| `verify.py` | your transcripts, the index | none | none |
| `agent.py` | writes `~/Library/LaunchAgents/…usemeup.plist`, runs `launchctl` | none | none |
| `server.py` | the above | binds `127.0.0.1` only | none |

`rate_limits.py` is the file to audit. The token is never written to disk, logged,
cached or returned, and everything the module returns passes through `_safe()`
first. It is read-only on your credential: it never writes to the Keychain and
never uses the refresh token, so it can't rotate or invalidate your Claude Code
login. It runs only with `USEMEUP_SOURCE=endpoint` or `auto`. The first time it
does, macOS asks whether it may read the Claude Code credential. That prompt is
the Keychain's, and denying it leaves the status line source as the way in.

`pricing.py` does a plain GET of a public price list. Nothing about you is sent,
and `USEMEUP_OFFLINE=1` turns it off.

The server probes the endpoint at most once every five minutes, page open or
closed, and backs off to as long as 30 minutes after an HTTP 429. Samples are kept
for 90 days in `~/.usemeup/usage.db`, a few megabytes at most.

## Keeping it running

The forecast and the daily buckets are only as complete as the hours the server
was up to sample. A Terminal window you close at night is a gap in tomorrow's
chart. On macOS:

```
usemeup agent install
```

writes a LaunchAgent (`~/Library/LaunchAgents/com.wiltonblake.usemeup.plist`) that
starts the server at login, restarts it if it exits, and keeps it serving with no
window open. It runs from the same Python and the same install you ran the
command from, so a source checkout has to stay where it is. Logs go to
`~/.usemeup/agent.log`. `usemeup agent status` shows whether it is loaded and
listening, and `usemeup agent uninstall` removes it.

Any UseMeUp server claims the port before it does anything else. If the port is
taken it waits in standby, trying again every 30 seconds, and scans and samples
nothing until the port is its own. So the agent and the menu bar app's bundled
server can never both be running the sampler, and the agent never exits just to
be restarted: when the other server stops, the agent takes over within 30
seconds. `usemeup agent status` says which process is serving.

### Auto-refresh spends your tokens, so it is off unless you ask

The Claude Code
access token lives 8 to 12 hours and only Claude Code refreshes it, on a real API
call. If you haven't used the CLI lately the token is stale and the endpoint
can't be read. With `USEMEUP_AUTO_REFRESH=1` the server runs `claude -p ok`
itself, at most once every ten minutes, so that Claude Code renews its own
credential. That costs a handful of Haiku tokens about once a day. A tool that
makes billable calls on your account shouldn't be something you find out about
later, so the plain `usemeup` command leaves it off. The agent turns it on,
because an unattended meter that goes stale twice a day defeats the point; pass
`--no-auto-refresh` to keep it read-only. Without it, the page tells you the
token expired and that `claude -p ok` fixes it. (`claude auth status` doesn't
refresh it. It only reads local state.)

## Configuration

| Variable | Effect |
|---|---|
| `USEMEUP_PORT` | server port (default 8787) |
| `USEMEUP_SOURCE` | `statusline` (default), `endpoint` or `auto` |
| `USEMEUP_AUTO_REFRESH=1` | allow the token refresh described above |
| `USEMEUP_TZ` | IANA zone for day bucketing (default: your system zone) |
| `USEMEUP_DB` | override the database location |
| `USEMEUP_OFFLINE=1` | never fetch live prices |
| `USEMEUP_DEMO=1` | replace project and session names with stable pseudonyms in `/api/usage` and the export |

## The transcript index, and why it left the page

UseMeUp started as a cost dashboard. It indexed the Claude Code and Cowork
transcripts on this Mac, priced every call at API list rates, and charted spend
by day, model, project and session. Those charts are gone from the page, and the
reason is worth writing down.

They were accurate about the wrong thing. Cloud sessions, Claude Code on the web
and cloud scheduled tasks run on Anthropic's servers and write nothing to your
disk. When my interactive work moved to the cloud, local transcripts went from
about a fifth of my indexed spend per month to 1%, while the account-wide meters
kept climbing. A chart that is blind to most of the work reads as "a quiet week"
when the week was anything but. And a subscription isn't billed per token
anyway, so the dollar figures were a size signal at best.

The index is still built, for one reason: the work-day model takes its weekday
shape from it. The command line tools over it still work if you want them.
`usemeup daily` prints the totals. `usemeup verify` recounts them from the raw
files with none of the indexing code, and exits non-zero on any disagreement; it
caught a deduplication bug that stored `output_tokens: 1` for a response that
finished at 56,354, and a day-bucketing bug that put about 10% of calls on the
wrong date. `usemeup prices` shows the price table, which resolves from the
[LiteLLM price database](https://github.com/BerriAI/litellm), first-party
Anthropic rows only, with a bundled fallback. `GET /api/export` dumps everything
as JSON.

Sources indexed: `~/.claude/projects/**/*.jsonl` for the CLI, and
`~/Library/Application Support/Claude/local-agent-mode-sessions/**/*.jsonl` for
Cowork runs executed on this machine. Unchanged files are skipped by mtime and
size, so a rescan of roughly 100k calls costs a `stat()` per file.

## License

MIT. See [LICENSE](LICENSE).

Not affiliated with, endorsed by, or sponsored by Anthropic.
