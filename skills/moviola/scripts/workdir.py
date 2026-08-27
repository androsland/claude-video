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
  * **The lock PATH is checked; the directory is not.** `.moviola.lock` is
    refused when it is a symlink, a directory, or anything else that is not a
    regular file, because truncating one of those destroys whatever it names.
    That check covers the FINAL component only — a symlinked `--out-dir` is an
    ordinary, legitimate thing to own and keeps working — and it says nothing
    about the parent being swapped underneath the run, which needs `dir_fd`
    relative opens throughout and is a different design. Nothing here detects
    it.
  * **The refusal is fenced against line forgery, not against a terminal.**
    `stderr_line` neutralizes line breaks and balances bidi marks; it does not
    touch ANSI, so a planted `started` field carrying `ESC[2K ESC[1G` can erase
    the line it was printed on and write something else over it. This is the
    first caller to feed `stderr_line` a value an attacker can plant BEFORE the
    run — everywhere else it fences the stderr of a subprocess moviola itself
    launched. Stripping ANSI belongs in `untrusted` if it belongs anywhere, and
    it is not obviously free: yt-dlp and ffmpeg colour their own output, and
    four other call sites pass it through today. Bounded, so the damage is one
    line rather than a screen; deliberately not fixed here.
  * **Refusals are for the WRONG KIND OF THING, not for ordinary failure.** A
    write that fails on ENOSPC or EIO still surfaces as a traceback: that is
    the user's own machine telling them something true, and dressing it up as a
    moviola refusal would hide where the problem is.
"""
from __future__ import annotations

import atexit
import errno
import json
import os
import stat
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

# The record is a file in a directory this run does not own, so its size is set
# by whoever wrote it rather than by this program. `untrusted`'s own NON-GOALS
# put bounding the input on the caller: `whisper._read_error_body` slices 400
# characters before fencing, and this is the same move one module along.
_MAX_RECORD = 64 * 1024
_MAX_STARTED = 200

# A pid is compared against this as a NUMBER and never rendered first. Since
# 3.10.7 CPython raises converting an int over `sys.get_int_max_str_digits()`
# to a string, so slicing `str(pid)` would crash on exactly the value the bound
# exists for — and that limit is no defence in its own right: it is absent
# before 3.10.7 and settable at runtime, which on an older 3.10 left the real
# ceiling at `_MAX_RECORD`, a 64KB stderr line. 20 digits covers every pid a
# 64-bit kernel can issue with room to spare.
_MAX_PID = 10 ** 20

# How many times to start over when the lock file is replaced underneath the
# acquire. Five is "a couple of unlucky interleavings", not a retry budget —
# anything that loses this race five times running is not the accidental second
# run this module is for, and saying so beats spinning.
_ACQUIRE_ATTEMPTS = 5


def _describe_holder(lock_path: Path) -> str:
    """Who holds the lock, in words safe to put in a refusal.

    Every value below came off a file this program did not necessarily write,
    so each is treated as a claim rather than a fact: the record may be absent,
    unreadable, enormous, not JSON, JSON of the wrong shape, or a dict whose
    fields are the wrong types, and each of those has to produce a SENTENCE
    rather than a traceback. A refusal that crashes while explaining itself is
    worse than the collision it was refusing.

    Absent is the ORDINARY case rather than the pathological one: the holder
    writes its record just AFTER taking the lock, so a run arriving inside that
    window finds an empty file and still has to say something useful. None of
    the branches below is an error path; every one of them returns prose.

    The parse stays here rather than moving into `untrusted`, and AGENTS.md
    can be read as forbidding that, so: `untrusted` holds edits and parses that
    are about the SHAPE of a foreign value — a line break, a bidi scope, a float
    that might be NaN — and are the same wherever they are applied. What follows
    is about this record's DOMAIN: which keys exist, that `pid` is an int and
    `started` a string, and what to say when they are not. Moving it would put
    moviola's lock format inside a module whose whole value is that it knows
    nothing about moviola. The pieces that ARE shape-level — the line fence, and
    the length bound this record needs before it reaches it — do come from
    there.
    """
    # `O_NOFOLLOW` and a bounded read rather than `read_text()`: the path is
    # somebody else's to point wherever they like, and a symlink to /dev/zero
    # is read forever by a call that has no size argument.
    try:
        fd = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return "another moviola run (its lock file could not be read)"
    try:
        blob = os.read(fd, _MAX_RECORD + 1)
    except OSError:
        return "another moviola run (its lock file could not be read)"
    finally:
        os.close(fd)

    if len(blob) > _MAX_RECORD:
        return "another moviola run (its lock file was implausibly large)"
    try:
        record = json.loads(blob.decode("utf-8") or "{}")
    except ValueError:  # includes UnicodeDecodeError
        return "another moviola run (its lock file could not be read)"
    if not isinstance(record, dict):
        return "another moviola run (its lock file was not the expected shape)"

    pid = record.get("pid")
    started = record.get("started")
    parts = []
    # `bool` is a subclass of `int`, so a bare `isinstance` check reports
    # `"pid": true` as "pid True" — a false statement about somebody else's
    # process, in a sentence whose only job is to be believed.
    if isinstance(pid, int) and not isinstance(pid, bool):
        # Out of range is not truncated to a plausible prefix: the first twenty
        # digits of a nonsense number READ like a pid, and a refusal that
        # invents one is worse than a refusal that admits it has nothing.
        parts.append(f"pid {pid}" if -_MAX_PID < pid < _MAX_PID else "an implausible pid")
    if isinstance(started, str) and started:
        parts.append(f"started {stderr_line(started[:_MAX_STARTED])}")
    if not parts:
        return "another moviola run (it had not recorded itself yet)"
    return f"another moviola run ({', '.join(parts)})"


def _refuse_planted(lock_path: Path, what: str) -> SystemExit:
    return SystemExit(
        f"[moviola] the lock file {lock_path} is {what}. moviola writes a "
        "regular file there and will not follow or truncate anything else — "
        "something other than moviola put this in the working directory. "
        "Remove it, or pass a different --out-dir."
    )


def _open_regular(lock_path: Path) -> int:
    """An fd on `lock_path`, which must be a regular file this run may truncate.

    `O_NOFOLLOW` covers the FINAL component only, which is exactly the split
    wanted here: a symlinked `--out-dir` is an ordinary thing to own — a
    `~/videos` pointing at another volume — and must keep working, while a
    symlinked `.moviola.lock` is not something moviola ever writes. Following
    one would not redirect the lock. The next statements ftruncate the fd to
    zero and write the record into it, so it would DESTROY whatever the link
    named, before this run has downloaded anything, and the `unlink()` on the
    way out would then remove the link and the evidence with it.

    The `fstat` is the other half, because `O_NOFOLLOW` says nothing about a
    FIFO or a directory and both reach real code: a directory raises out of
    `os.open`, a FIFO opens fine under `O_RDWR` and raises EINVAL out of
    `os.ftruncate`, several lines past the only `except` in `exclusive`. Both
    escaped `main()` as tracebacks naming ftruncate rather than the planted
    file — so a directory a stranger can write to was a denial of service on
    every run pointed at it, described in terms of the wrong thing.
    """
    try:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        # ELOOP is Linux's answer; EMLINK is the BSD and macOS spelling.
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            raise _refuse_planted(lock_path, "a symbolic link") from None
        if exc.errno == errno.EISDIR:
            raise _refuse_planted(lock_path, "a directory") from None
        raise
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise _refuse_planted(lock_path, "not a regular file")
    return fd


def _names_this_inode(lock_path: Path, fd: int) -> bool:
    """Does `lock_path` still name the file `fd` is open on?"""
    try:
        on_disk = os.stat(lock_path)
    except OSError:
        return False
    here = os.fstat(fd)
    return (on_disk.st_dev, on_disk.st_ino) == (here.st_dev, here.st_ino)


def _acquire(lock_path: Path, work: Path) -> int:
    """An fd holding an exclusive lock on the file `lock_path` NAMES.

    "Names" is the load-bearing word. `flock` locks an inode, and between the
    `os.open` and the `flock` a statement later the holder can finish and
    unlink the entry this run just opened. The lock then succeeds on a file
    with no name; the next arrival opens the path, creates a fresh inode, locks
    THAT, and the two run concurrently — two runs, two inodes, each convinced
    it owns the directory. That is the mixed report this module exists to
    prevent, reached through the module itself, so the inode is re-checked
    against the path once the lock is held and a run that lost the race starts
    over instead of holding an orphan.
    """
    for _attempt in range(_ACQUIRE_ATTEMPTS):
        fd = _open_regular(lock_path)
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
            if _names_this_inode(lock_path, fd):
                return fd
        except BaseException:
            os.close(fd)
            raise
        # Lost the race: what this run locked is not what the path names now.
        os.close(fd)

    raise SystemExit(
        f"[moviola] the lock file in {work} was replaced {_ACQUIRE_ATTEMPTS} times "
        "while this run tried to take it, so moviola cannot tell whether it owns "
        "this working directory. Something other than a second moviola run is "
        "writing into it. Pass a different --out-dir."
    )


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
    fd = _acquire(lock_path, work)
    try:
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps({
            "pid": os.getpid(),
            "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }).encode())
        os.fsync(fd)
        try:
            yield
        finally:
            # Unlink BEFORE releasing. This does not CLOSE the window — an
            # arrival that already opened this inode can still lock it the
            # moment LOCK_UN lands, whichever order the two go in, and closing
            # that is `_acquire`'s inode re-check rather than this ordering.
            # What it does is narrow it, at no cost. Nothing ever waits here:
            # the lock is taken `LOCK_NB`, so an arrival fails immediately.
            #
            # And only if the path still names THIS inode. A lock file replaced
            # during the run belongs to whoever replaced it, and unlinking by
            # path would take somebody else's lock out from under them.
            try:
                if _names_this_inode(lock_path, fd):
                    lock_path.unlink()
            except OSError:
                pass
            # Redundant for release: `os.close` below drops the kernel lock by
            # itself, which is this module's whole premise. Kept so the release
            # is a statement someone can read rather than a side effect of
            # cleanup, and so a path that ever dups or inherits this fd does not
            # silently extend the hold past here.
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def hold(work: Path) -> None:
    """Take the lock for the REST OF THE PROCESS, releasing it at exit.

    `main()` is the whole program in PRODUCTION — `raise SystemExit(main())` is
    its only caller there — so "until this process ends" and "for the duration
    of the run" are the same span. Saying it this way keeps the call site one
    line instead of indenting the whole of `main` under a `with`, and a reindent
    that large is the kind of diff a real change hides inside.

    Cleanup is `atexit`, which covers a normal return and every `SystemExit`
    moviola raises. What it does NOT cover is SIGKILL, and that is survivable in
    a way a pid file would not be: the kernel drops the `flock` regardless, so
    the worst case is a stray empty `.moviola.lock` that the next run opens and
    locks without noticing. Nothing has to expire it.

    NON-GOAL, and it is a live one rather than a hypothetical: "the rest of the
    process" is not "the rest of the run" for an IN-PROCESS caller. Seven tests
    call `main()` directly, and each one registers an `atexit` that holds its
    flock until the interpreter exits — so a test calling `main()` twice against
    one `--out-dir` refuses itself on the second call, and the lock outlives
    every test in the session rather than the run that took it. That is filed in
    TODOS.md; today it does not bite because no test reuses a directory, which
    is a property of the tests and not of this function.
    """
    stack = ExitStack()
    stack.enter_context(exclusive(work))
    atexit.register(stack.close)
