#!/usr/bin/env python3
"""Exclusive ownership of the working directory, for the span of one run.

`--out-dir` is a documented flag and the skill tells the agent to reuse the
directory, so two runs pointed at one directory is a thing a user does by
accident rather than an exotic case. What happens then is not a crash. They
overwrite each other's `video.*` and `frame_*.jpg` outright, and
`download.snapshot_dir` — which answers "did THIS run produce this file" by
comparing (mtime, size) against a snapshot taken before yt-dlp starts — reads
the other run's freshly-written file as this run's, because it is new since the
snapshot. The result is a report assembled from two different films with
nothing anywhere saying so, which is the same failure as every other one on
this branch: not silence, but a plausible answer about the wrong thing.

Refusing rather than disclosing is the deliberate exception to this codebase's
"disclosure, not strictness" habit, and the reason is that disclosure cannot
help here. Telling the user about the collision and continuing still produces
the mixed report; the only thing worth saying is said BEFORE any work happens,
while there is still one clean run to protect.

It is `flock` and not a pid file because the kernel drops the lock when the fd
closes — process exit, crash, SIGKILL, all of them. There is no stale-lock
state to expire and nothing has to decide whether a recorded pid is still
alive, a decision that is wrong the moment the pid is reused.

NON-GOALS, stated because an unstated limit reads as a claim of coverage:

  * **Advisory, and POSIX-only.** A host without `fcntl` gets no lock at all,
    and it says so on stderr rather than letting the absence pass for a guard.
    `flock` is also a no-op on many NFS mounts, and that one is undetectable
    from here.
  * **Not a concurrency primitive.** It guards against the accident described
    above — the same user starting a second run — not against a process that
    declines to take the lock, or one that is not moviola at all. Anything else
    writing into the directory is invisible to it exactly as before.
  * **It does not make the directory safe to share.** It makes sharing FAIL
    LOUDLY. The fix for a user who wants two runs at once is still two
    directories, and the refusal says so.
  * **It says nothing about what is already in the directory.** A stale
    `video.mp4` from a previous, finished run is a different problem, and
    `snapshot_dir` is the thing that answers it.
"""
from __future__ import annotations

import atexit
import json
import os
import sys
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from untrusted import stderr_line  # noqa: E402

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX-only; see NON-GOALS
    fcntl = None  # type: ignore[assignment]

LOCK_NAME = ".moviola.lock"


def _describe_holder(lock_path: Path) -> str:
    """Who says they hold `lock_path`, phrased for a stderr line.

    The record is read back off a directory this run does NOT own — that is the
    entire premise — so it is somebody else's output even though moviola wrote
    the last one, and it is fenced and parsed guardedly like any other. A record
    that is missing, truncated, or not JSON is the normal case rather than an
    error: the holder writes it just AFTER taking the lock, so a run arriving in
    that window finds an empty file and must still say something useful.
    """
    try:
        record = json.loads(lock_path.read_text() or "{}")
    except (OSError, ValueError):
        return "another moviola run (its lock file could not be read)"
    if not isinstance(record, dict):
        return "another moviola run (its lock file was not the expected shape)"

    pid = record.get("pid")
    started = record.get("started")
    parts = []
    if isinstance(pid, int):
        parts.append(f"pid {pid}")
    if isinstance(started, str) and started:
        parts.append(f"started {stderr_line(started)}")
    if not parts:
        return "another moviola run (it had not recorded itself yet)"
    return f"another moviola run ({', '.join(parts)})"


@contextmanager
def exclusive(work: Path) -> Iterator[None]:
    """Hold `work` for the caller's whole span, or refuse to start.

    Yields on success and releases on the way out, including when the body
    raises — moviola exits through `SystemExit` on most failure paths, and a
    lock that outlived one would make the NEXT run refuse for a reason that no
    longer exists.
    """
    if fcntl is None:
        print(
            "[moviola] no fcntl on this platform, so the working directory is NOT "
            "locked — a second run sharing this --out-dir would overwrite this "
            "one's files and be reported as part of it.",
            file=sys.stderr,
        )
        yield
        return

    lock_path = work / LOCK_NAME
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SystemExit(
                f"[moviola] {_describe_holder(lock_path)} is already using this "
                f"working directory: {work}. Two runs sharing one --out-dir "
                "overwrite each other's video.* and frame_*.jpg, and the "
                "stale-file guard cannot tell whose file is whose — so this run "
                "is stopping rather than reporting on a mix of both. Pass a "
                "different --out-dir, or wait for the other run to finish."
            )

        os.ftruncate(fd, 0)
        os.write(fd, json.dumps({
            "pid": os.getpid(),
            "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }).encode())
        os.fsync(fd)
        try:
            yield
        finally:
            # Unlink BEFORE releasing. The other order leaves a window in which a
            # waiting run takes the lock on an inode this one is about to delete,
            # and then holds a lock on a file no new arrival will ever open.
            try:
                lock_path.unlink()
            except OSError:
                pass
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def hold(work: Path) -> None:
    """Take the lock for the REST OF THE PROCESS, releasing it at exit.

    `main()` IS the whole program — `raise SystemExit(main())` is its only
    caller — so "until this process ends" and "for the duration of the run" are
    the same span. Saying it this way keeps the call site one line instead of
    indenting the whole of `main` under a `with`, and a reindent that large is
    the kind of diff a real change hides inside.

    Cleanup is `atexit`, which covers a normal return and every `SystemExit`
    moviola raises. What it does NOT cover is SIGKILL, and that is survivable in
    a way a pid file would not be: the kernel drops the `flock` regardless, so
    the worst case is a stray empty `.moviola.lock` that the next run opens and
    locks without noticing. Nothing has to expire it.
    """
    stack = ExitStack()
    stack.enter_context(exclusive(work))
    atexit.register(stack.close)
