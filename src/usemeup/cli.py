#!/usr/bin/env python3
"""
cli.py - the usemeup command.

    usemeup                 index transcripts and serve the dashboard
    usemeup serve           the same, with flags
    usemeup verify          recount from raw transcripts and check the index
    usemeup ingest          re-scan transcripts without starting the server
    usemeup daily           a terminal table, no browser needed
    usemeup prices          show the resolved price table and where it came from
"""
import argparse
import sys

from . import config


def _fmt_usd(v):
    return "$%.2f" % v if v >= 1 else "$%.3f" % v


def _fmt_n(n):
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= div:
            return "%.1f%s" % (n / div, unit)
    return str(int(n))


def cmd_serve(args):
    if args.port:
        config.PORT = args.port
    if args.demo:
        config.DEMO = True
    from . import server
    server.PORT = config.PORT
    print("scanning transcripts…", flush=True)
    info = server._maybe_ingest(force=True)
    if info and "error" not in info:
        print("  %s calls indexed (%s files read, %s unchanged, %ss)" % (
            format(info["total_calls"], ","), format(info["files_scanned"], ","),
            format(info["files_unchanged"], ","), info["seconds"]), flush=True)
    else:
        print("  ingest problem: %s" % (info or {}).get("error"), flush=True)
    print(config.banner())
    if not args.no_open:
        import threading
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(
            "http://127.0.0.1:%d/" % config.PORT)).start()
    try:
        server.Server(("127.0.0.1", config.PORT), server.Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


def cmd_verify(args):
    from . import verify
    sys.argv = ["usemeup verify"]
    if args.days:
        sys.argv += ["--days", str(args.days)]
    if args.day:
        sys.argv += ["--day", args.day]
    return verify.main()


def cmd_ingest(args):
    from . import store
    def show(done, total, added):
        print("  %d/%d files, %d rows written" % (done, total, added), flush=True)
    info = store.ingest(show)
    print("%s calls indexed (%s files read, %s unchanged, %ss)" % (
        format(info["total_calls"], ","), format(info["files_scanned"], ","),
        format(info["files_unchanged"], ","), info["seconds"]))
    return 0


def cmd_daily(args):
    from . import parse_usage
    d = parse_usage.build()
    days = sorted(d["by_day"])[-args.days:]
    if not days:
        print("no usage found.")
        return 0
    print("%-12s %8s %10s %12s %12s" % ("day", "calls", "output", "cache read", "notional"))
    print("-" * 58)
    for day in days:
        v = d["by_day"][day]
        print("%-12s %8d %10s %12s %12s" % (
            day, v["calls"], _fmt_n(v["output"]), _fmt_n(v["cache_read"]), _fmt_usd(v["cost"])))
    t = d["totals"]
    print("-" * 58)
    print("%-12s %8d %10s %12s %12s" % (
        "all time", t["calls"], _fmt_n(t["output"]), _fmt_n(t["cache_read"]), _fmt_usd(t["cost"])))
    print("\ndays are cut in %s. Notional spend applies API list prices to your token"
          "\ncounts; a subscription is not billed this way." % d["timezone"])
    return 0


def cmd_prices(args):
    from . import pricing
    table, meta = pricing.load(force=args.refresh)
    print("source: %s" % meta.get("source"))
    if meta.get("age_hours") is not None:
        print("age   : %.1f hours" % meta["age_hours"])
    if meta.get("error"):
        print("note  : %s" % meta["error"])
    print("url   : %s" % meta.get("url", ""))
    print("models: %d\n" % meta.get("models", 0))
    hdr = "%-30s %9s %9s %9s %9s %9s"
    print(hdr % ("model", "input", "output", "write 5m", "write 1h", "read"))
    for k in sorted(table):
        v = table[k]
        print(hdr % (k[:30], "%.4g" % v["input"], "%.4g" % v["output"],
                     "%.4g" % v.get("cache_write_5m", 0), "%.4g" % v.get("cache_write_1h", 0),
                     "%.4g" % v.get("cache_read", 0)))
    print("\nall figures are USD per million tokens.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="usemeup",
        description="A local Claude usage dashboard that can prove its own numbers.")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="index transcripts and serve the dashboard")
    s.add_argument("--port", type=int, help="default 8787, or USEMEUP_PORT")
    s.add_argument("--demo", action="store_true",
                   help="redact project and session names, for screenshots")
    s.add_argument("--no-open", action="store_true", help="do not open a browser")
    s.set_defaults(fn=cmd_serve)

    v = sub.add_parser("verify", help="recount from raw transcripts and check the index")
    v.add_argument("--days", type=int, default=0, help="only the most recent N days")
    v.add_argument("--day", help="a single YYYY-MM-DD")
    v.set_defaults(fn=cmd_verify)

    i = sub.add_parser("ingest", help="re-scan transcripts without serving")
    i.set_defaults(fn=cmd_ingest)

    dd = sub.add_parser("daily", help="a terminal table of daily usage")
    dd.add_argument("--days", type=int, default=14, help="how many days to show")
    dd.set_defaults(fn=cmd_daily)

    pr = sub.add_parser("prices", help="show the resolved price table and its source")
    pr.add_argument("--refresh", action="store_true", help="force a re-fetch")
    pr.set_defaults(fn=cmd_prices)

    args = ap.parse_args(argv)
    if not args.cmd:
        # Bare `usemeup` serves, which is what almost everyone wants.
        args = ap.parse_args(["serve"])
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
