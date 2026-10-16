"""Private state locations and a fail-fast, cross-platform ledger lock."""

import contextlib
import os
import sys
from pathlib import Path


def configure_stdio():
    """Keep JSON and Chinese CLI text UTF-8 on Windows as well as Unix."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def state_dir():
    configured = os.environ.get("YNP_STATE_DIR")
    if configured:
        return Path(configured).expanduser()
    base = os.environ.get("XDG_STATE_HOME")
    if not base and os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / ".local" / "state") / "yuque-note-publisher"


@contextlib.contextmanager
def ledger_lock(path):
    """Do not allow concurrent processes to reserve the same API budget slot."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".lock")
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(
            "USAGE_LEDGER_BUSY: another process owns the ledger lock; "
            "stop concurrent requests. After a crash, verify no publisher is running "
            "before manually removing the stale .lock file."
        ) from exc
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        fd = None
        yield
    finally:
        if fd is not None:
            os.close(fd)
        lock.unlink()
