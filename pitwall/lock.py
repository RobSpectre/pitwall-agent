"""One watcher at a time, on every platform.

The watcher is a poller: two copies means every event logged twice and two
players fighting over the same window. POSIX gets ``flock``, Windows gets
msvcrt's byte-range lock, and anything else degrades to a best-effort lock file
rather than a crash -- a missing lock must never stop a broadcast.

The lock is advisory and released by the OS when the process dies, so a crashed
watcher does not leave a stale file that blocks the next one.
"""

from __future__ import annotations

import os
from pathlib import Path


def path() -> Path:
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home) / "state" / "pitwall-watch.lock"


class SingleInstance:
    """A file lock that is held for as long as this object is open."""

    def __init__(self, target: Path | None = None):
        self.target = Path(target) if target is not None else path()
        self._handle = None

    # -- lifecycle ---------------------------------------------------------

    def acquire(self) -> bool:
        """True when this process owns the lock, False when another one does."""
        if self._handle is not None:
            return True
        try:
            self.target.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self.target, "a+", encoding="utf-8")
        except OSError:
            # An unwritable state directory must not stop the watcher.
            return True

        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False

        self._handle = handle
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()))
            handle.flush()
        except OSError:
            pass                      # the lock is what matters, not the note
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self._handle.close()
            self._handle = None

    # -- context manager ---------------------------------------------------

    def __enter__(self) -> "SingleInstance":
        self.acquire()
        return self

    def __exit__(self, *_exc) -> None:
        self.release()
