"""status.json: the file other tools read instead of the HTTP server."""
import datetime as _dt
import json
import os
import tempfile

from usemeup import status
from test_panel import _window

UTC = _dt.timezone.utc


def _limits(**over):
    d = {"ok": True, "checked_at": _dt.datetime.now(UTC).isoformat(), "strategy": "usage endpoint",
         "windows": [_window(60, 0.5, projected_end=120)]}
    d.update(over)
    return d


def test_the_file_carries_what_a_notch_panel_draws():
    body = status.build({}, _limits())
    w = body["windows"][0]
    # The fields Transom's UsagePanel decodes, all present.
    for k in ("key", "label", "used_pct", "verdict", "state", "kicker", "detail", "basis",
              "t0", "t1", "now", "y_max", "over_cap", "reference", "observed", "projection"):
        assert k in w, k
    assert w["advice"] and body["alerts"][0]["kind"] == "forecast"
    assert body["schema"] == status.SCHEMA and body["fresh_for_seconds"] == status.FRESH_FOR_SECONDS


def test_write_is_atomic_and_readable_by_anyone():
    d = tempfile.mkdtemp()
    p = status.write(status.build({}, _limits()), os.path.join(d, "status.json"))
    with open(p) as f:
        assert json.load(f)["ok"] is True
    assert oct(os.stat(p).st_mode & 0o777) == "0o644"
    assert [n for n in os.listdir(d) if n.startswith(".status-")] == []


def test_fresh_while_checked_recently_and_not_after():
    body = status.build({}, _limits())
    now = _dt.datetime.fromisoformat(body["checked_at"])
    assert status.is_fresh(body, now + _dt.timedelta(minutes=44))
    assert not status.is_fresh(body, now + _dt.timedelta(minutes=46))


def test_no_data_is_never_fresh():
    body = status.build({}, {"ok": False, "error": "no credential"})
    assert body["ok"] is False and body["windows"] == [] and body["error"] == "no credential"
    assert not status.is_fresh(body)


def test_last_good_figures_are_marked_stale():
    assert status.build({}, _limits(stale=True))["stale"] is True
