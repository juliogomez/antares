#!/usr/bin/env python3
"""Start a long-running command that survives the terminal that launched it.

`nohup cmd &` plus `disown` is not enough on macOS when the launching process group
gets torn down: the child stays in that group and dies with it. macOS also has no
`setsid(1)`. So we double-fork, call `os.setsid()` in the intermediate child, and let
the grandchild be reparented to init with its own session.

This matters here because the 500-pair sweep runs for hours and must not depend on a
terminal staying open.

Usage:
    python scripts/daemon.py --name sweep-antares -- python scripts/run_eval.py --model antares --subset full
    python scripts/daemon.py --status sweep-antares
    python scripts/daemon.py --stop   sweep-antares
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / "runs" / "logs"


def paths(name: str) -> tuple[Path, Path]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR / f"{name}.log", LOG_DIR / f"{name}.pid"


def read_pid(pidfile: Path) -> int | None:
    if not pidfile.exists():
        return None
    try:
        return int(pidfile.read_text().strip())
    except ValueError:
        return None


def alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start(name: str, cmd: list[str], append: bool) -> int:
    log, pidfile = paths(name)
    existing = read_pid(pidfile)
    if alive(existing):
        print(f"{name} already running (pid {existing})")
        return 0

    read_fd, write_fd = os.pipe()
    if os.fork() == 0:
        os.close(read_fd)
        os.setsid()
        with open(log, "a" if append else "w") as fh, open(os.devnull) as devnull:
            proc = subprocess.Popen(
                cmd, cwd=REPO_ROOT, stdin=devnull, stdout=fh, stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
        os.write(write_fd, str(proc.pid).encode())
        os.close(write_fd)
        os._exit(0)

    os.close(write_fd)
    os.wait()
    child_pid = int(os.read(read_fd, 32).decode() or 0)
    os.close(read_fd)

    pidfile.write_text(str(child_pid))
    time.sleep(1)
    if not alive(child_pid):
        print(f"{name} exited immediately; see {log}", file=sys.stderr)
        print("".join(log.read_text().splitlines(keepends=True)[-15:]), file=sys.stderr)
        return 1
    print(f"{name} started (pid {child_pid})\n  log: {log}")
    return 0


def status(name: str) -> int:
    log, pidfile = paths(name)
    pid = read_pid(pidfile)
    print(f"{name}: {'running' if alive(pid) else 'not running'} (pid {pid})  log: {log}")
    return 0 if alive(pid) else 1


def stop(name: str) -> int:
    """Terminate the whole process group, not just the process we recorded.

    Signalling only the tracked pid is not enough and the failure is nasty. Our sweep
    wrapper spawns the benchmark CLI as a child; killing the wrapper orphans that child,
    which is reparented to init and keeps running invisibly. One of ours survived a stop,
    outlived the model server it depended on, and then wrote 960 results in four minutes:
    every request failing instantly with connection refused, every failure recorded by the
    harness as a completed zero-tool-call run, and therefore as a Phase B true negative.
    Because start() puts the job in its own session via os.setsid(), the job is its own
    process group leader and we can signal the entire group.
    """
    _, pidfile = paths(name)
    pid = read_pid(pidfile)
    if not alive(pid):
        print(f"{name}: not running")
        return 0

    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = None

    def signal_all(sig: int) -> None:
        if pgid is not None and pgid != os.getpgid(0):
            try:
                os.killpg(pgid, sig)
                return
            except OSError:
                pass
        try:
            os.kill(pid, sig)
        except OSError:
            pass

    signal_all(signal.SIGTERM)
    for _ in range(20):
        if not alive(pid):
            break
        time.sleep(0.5)
    else:
        signal_all(signal.SIGKILL)
        time.sleep(1)

    # The group can outlive the leader, so verify rather than trust.
    stragglers = []
    if pgid is not None:
        out = subprocess.run(["ps", "-o", "pid=", "-g", str(pgid)], capture_output=True, text=True)
        stragglers = [int(p) for p in out.stdout.split() if p.strip().isdigit() and int(p) != os.getpid()]
    if stragglers:
        print(f"{name}: {len(stragglers)} process(es) survived the group signal: {stragglers}")
        for p in stragglers:
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass
    print(f"{name}: stopped")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", help="job name (used for the log and pid files)")
    ap.add_argument("--status", metavar="NAME")
    ap.add_argument("--stop", metavar="NAME")
    ap.add_argument("--append", action="store_true", help="append to the log instead of truncating")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    args = ap.parse_args()

    if args.status:
        return status(args.status)
    if args.stop:
        return stop(args.stop)
    cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    if not args.name or not cmd:
        ap.error("need --name NAME -- COMMAND...")
    return start(args.name, cmd, args.append)


if __name__ == "__main__":
    sys.exit(main())
