#!/usr/bin/env python3
"""
agent.py - keep the dashboard running as a macOS launchd LaunchAgent.

    usemeup agent install     write the plist and start it; restarts at login and on crash
    usemeup agent uninstall   stop it and remove the plist
    usemeup agent status      is it loaded, is it listening, where are the logs

Why this exists. The daily view only knows about usage that was sampled while
the server was running, and the server only samples while a Terminal window is
open. A LaunchAgent runs it at login, restarts it if it dies, and keeps it
running with the window closed. That is the difference between a chart with
bracketed gaps and one without.

What the agent runs is exactly what you would type: this same Python, this same
package, `serve --no-open`. Nothing is copied anywhere; the plist points at the
install you ran the command from. Move or delete that install and the agent
stops working, which `status` will tell you.

Auto-refresh is ON in the agent by default (pass --no-auto-refresh to keep it
off), because an unattended meter that goes stale every 8 to 12 hours is not
much of a meter. It spends a few Haiku tokens about once a day; see README.

macOS only. launchd does not exist elsewhere, and the command says so.
"""
import os
import plistlib
import subprocess
import sys

from . import config

LABEL = "com.wiltonblake.usemeup"
AGENT_DIR = os.path.expanduser("~/Library/LaunchAgents")
PLIST = os.path.join(AGENT_DIR, LABEL + ".plist")
LOG = os.path.join(config.DB_DIR, "agent.log")
ERR = os.path.join(config.DB_DIR, "agent.err")

# Where `claude` tends to live. The agent's PATH is built from these plus the
# installing shell's PATH, so the token refresh can find the CLI.
_PATH_HINTS = [os.path.expanduser("~/.local/bin"), "/opt/homebrew/bin", "/usr/local/bin",
               "/usr/bin", "/bin", "/usr/sbin", "/sbin"]


def _domain():
    return "gui/%d" % os.getuid()


def _launchctl(*args):
    out = subprocess.run(["launchctl"] + list(args), capture_output=True, text=True)
    return out.returncode, (out.stdout or "") + (out.stderr or "")


def _agent_path():
    parts = []
    for p in os.environ.get("PATH", "").split(os.pathsep) + _PATH_HINTS:
        if p and p not in parts:
            parts.append(p)
    return os.pathsep.join(parts)


def _src_root():
    """The src/ directory when running from a checkout, else None."""
    here = os.path.dirname(os.path.abspath(__file__))       # .../src/usemeup
    src = os.path.dirname(here)
    if os.path.basename(here) == "usemeup" and os.path.basename(src) == "src":
        return src
    return None


def build_plist(auto_refresh=True, port=None):
    env = {"PATH": _agent_path(), "HOME": os.path.expanduser("~")}
    src = _src_root()
    if src:
        env["PYTHONPATH"] = src
    if auto_refresh:
        env["USEMEUP_AUTO_REFRESH"] = "1"
    if port or os.environ.get("USEMEUP_PORT"):
        env["USEMEUP_PORT"] = str(port or os.environ["USEMEUP_PORT"])
    for k in ("USEMEUP_TZ", "USEMEUP_DB", "USEMEUP_OFFLINE", "USEMEUP_DEMO"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, "-m", "usemeup.cli", "serve", "--no-open"],
        "EnvironmentVariables": env,
        "RunAtLoad": True,
        "KeepAlive": True,               # restart on exit, whatever the reason
        "ThrottleInterval": 10,          # but not in a tight loop
        "ProcessType": "Background",
        "StandardOutPath": LOG,
        "StandardErrorPath": ERR,
    }


def _listening(port):
    import socket
    s = socket.socket()
    s.settimeout(0.5)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def install(auto_refresh=True, port=None):
    if sys.platform != "darwin":
        print("usemeup agent needs macOS launchd; on this platform run `usemeup` under "
              "your own service manager (systemd, a scheduled task, tmux).")
        return 2
    os.makedirs(AGENT_DIR, exist_ok=True)
    os.makedirs(config.DB_DIR, exist_ok=True)
    plist = build_plist(auto_refresh=auto_refresh, port=port)
    if os.path.exists(PLIST):
        _launchctl("bootout", _domain(), PLIST)       # replace a previous install cleanly
    with open(PLIST, "wb") as f:
        plistlib.dump(plist, f)
    rc, msg = _launchctl("bootstrap", _domain(), PLIST)
    if rc != 0:
        print("wrote %s but launchctl bootstrap failed (%d): %s" % (PLIST, rc, msg.strip()))
        return 1
    p = int(plist["EnvironmentVariables"].get("USEMEUP_PORT", config.PORT))
    print("installed %s" % PLIST)
    print("  runs   : %s" % " ".join(plist["ProgramArguments"]))
    if "PYTHONPATH" in plist["EnvironmentVariables"]:
        print("  from   : %s (a source checkout; keep it where it is)"
              % plist["EnvironmentVariables"]["PYTHONPATH"])
    print("  refresh: %s" % ("ON" if auto_refresh else "off"))
    print("  logs   : %s" % LOG)
    print("  url    : http://127.0.0.1:%d/" % p)
    print("It starts at login and restarts if it exits. `usemeup agent status` to check, "
          "`usemeup agent uninstall` to remove.")
    print("If macOS asks whether Python may use the Claude Code credential, allow it; "
          "that dialog is the Keychain, not this tool.")
    return 0


def uninstall():
    if not os.path.exists(PLIST):
        print("nothing installed at %s" % PLIST)
        return 0
    rc, msg = _launchctl("bootout", _domain(), PLIST)
    os.remove(PLIST)
    print("removed %s%s" % (PLIST, "" if rc == 0 else " (it was not running: %s)" % msg.strip()))
    return 0


def status():
    if not os.path.exists(PLIST):
        print("not installed. `usemeup agent install` sets it up.")
        return 1
    with open(PLIST, "rb") as f:
        plist = plistlib.load(f)
    env = plist.get("EnvironmentVariables", {})
    port = int(env.get("USEMEUP_PORT", config.PORT))
    rc, out = _launchctl("print", "%s/%s" % (_domain(), LABEL))
    loaded = rc == 0
    state, pid = "unknown", None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("state = "):
            state = line.split("=", 1)[1].strip()
        elif line.startswith("pid = "):
            pid = line.split("=", 1)[1].strip()
    exe = plist["ProgramArguments"][0]
    print("plist   : %s" % PLIST)
    print("loaded  : %s%s" % ("yes" if loaded else "no",
                               (", state %s, pid %s" % (state, pid)) if loaded else ""))
    print("serving : %s on 127.0.0.1:%d" % ("yes" if _listening(port) else "NO", port))
    print("python  : %s%s" % (exe, "" if os.path.exists(exe) else "  (MISSING)"))
    src = env.get("PYTHONPATH")
    if src:
        print("source  : %s%s" % (src, "" if os.path.isdir(src) else "  (MISSING)"))
    print("refresh : %s" % ("ON" if env.get("USEMEUP_AUTO_REFRESH") else "off"))
    print("logs    : %s" % LOG)
    try:
        with open(LOG) as f:
            tail = f.read().strip().splitlines()[-4:]
        if tail:
            print("last log lines:")
            for t in tail:
                print("  " + t)
    except OSError:
        pass
    return 0 if loaded and _listening(port) else 1
