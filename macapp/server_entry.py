#!/usr/bin/env python3
"""PyInstaller entry point for the server bundled inside UseMeUp.app.

The app only ever starts this when nothing is already listening on 8787, so it
takes the same arguments the LaunchAgent does and shares the same index at
~/.usemeup/usage.db. Two servers sampling into one sqlite file would race, which
is why the app attaches to an existing one rather than always spawning.
"""
import multiprocessing
import sys


def main() -> int:
    multiprocessing.freeze_support()
    from usemeup.cli import main as cli_main
    return cli_main(["serve", "--no-open"])


if __name__ == "__main__":
    sys.exit(main())
