"""Read-only loading of Inspect ``.eval`` logs.

This module is the only place ``inspect-replay`` touches the filesystem, and it
only ever reads. It never writes, moves, or mutates a log.
"""

from __future__ import annotations

from pathlib import Path

from inspect_ai.log import EvalLog, read_eval_log

__all__ = ["LoadError", "load_log"]


class LoadError(Exception):
    """A log could not be read as an Inspect evaluation log.

    Raised with a message naming the path and the underlying reason, so that a
    malformed or non-Inspect file produces a clear diagnostic rather than a
    stack trace or, worse, a silently empty comparison.
    """

    def __init__(self, path: str | Path, reason: str) -> None:
        self.path = str(path)
        self.reason = reason
        super().__init__(f"could not read Inspect log '{path}': {reason}")


def load_log(path: str | Path) -> EvalLog:
    """Read an Inspect ``.eval`` (or ``.json``) log.

    Samples are read in full, because sample-level comparison needs them. A
    header-only log (one written with ``log_samples=False``) loads fine; the
    sample comparison then reports that no samples were recorded rather than
    reporting zero differences.

    Raises:
        LoadError: the path does not exist, is a directory, or does not parse
            as an Inspect log.
    """
    p = Path(path)
    if not p.exists():
        raise LoadError(p, "file does not exist")
    if p.is_dir():
        raise LoadError(p, "path is a directory, not a log file")
    if p.stat().st_size == 0:
        raise LoadError(p, "file is empty")

    try:
        log = read_eval_log(str(p))
    except Exception as exc:
        raise LoadError(p, _explain(exc)) from exc

    # read_eval_log is tolerant of some malformed input. Guard the invariants
    # the rest of the tool relies on rather than discovering them downstream.
    if not isinstance(log, EvalLog):
        raise LoadError(p, f"parsed to {type(log).__name__}, not an EvalLog")
    if getattr(log, "eval", None) is None:
        raise LoadError(p, "log has no 'eval' section (not an Inspect eval log)")

    if not log.location:
        log.location = str(p)
    return log


def _explain(exc: Exception) -> str:
    """Turn a reader exception into something a user can act on.

    inspect_ai's own errors describe the file format ("EOCD not found" means the
    zip central directory is missing), which is accurate and useless to someone
    who just wants to know that they pointed at the wrong file.
    """
    text = str(exc)
    if "EOCD" in text or "BadZipFile" in type(exc).__name__:
        return (
            "the file is not a valid .eval archive (it may be truncated, corrupt, or not an "
            "Inspect log at all)"
        )
    if "No recorder for location" in text:
        return "the file extension is not one Inspect can read (expected .eval or .json)"
    if type(exc).__name__ == "ValidationError":
        first = text.splitlines()[1].strip() if len(text.splitlines()) > 1 else text
        return f"the file parsed but is not a valid Inspect log ({first})"
    return f"{type(exc).__name__}: {text.splitlines()[0]}"
