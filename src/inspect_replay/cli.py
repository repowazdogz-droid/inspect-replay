"""Command line interface.

Exit codes are part of the contract, so that CI can act on the result:

* ``0`` -- no differences found
* ``1`` -- differences found
* ``2`` -- the comparison could not be performed (bad path, malformed log, or
  no sample could be aligned)

Exit 2 covers the case where the tool could not answer the question, not only
the case where it could not read the file. A comparison that aligned no samples
has established nothing about the evaluation, and returning 0 there would pass a
CI gate on a run nobody looked at.

``--exit-zero`` forces ``0`` on a successful comparison, for pipelines that
want the report without failing the build. It does NOT suppress exit 2: a
comparison that could not be performed is an error, not a difference.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import __version__
from .compare import compare
from .json_output import to_json
from .loader import LoadError
from .models import Verdict
from .report import render

__all__ = ["main"]

EXIT_NO_DIFFERENCES = 0
EXIT_DIFFERENCES = 1
EXIT_ERROR = 2
"""Also returned when no sample could be aligned: the tool could not answer."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="inspect-replay",
        description=(
            "Compare two Inspect AI evaluation logs and show what changed in "
            "configuration, samples, scores, and errors."
        ),
        epilog=(
            "Exit codes: 0 no differences, 1 differences found, 2 comparison failed. "
            "inspect-replay is read-only and never re-runs an evaluation."
        ),
    )
    parser.add_argument("--version", action="version", version=f"inspect-replay {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    cmp_parser = sub.add_parser(
        "compare",
        help="compare two .eval logs",
        description="Compare two Inspect .eval logs. Read-only.",
    )
    cmp_parser.add_argument("old", help="path to the baseline .eval log")
    cmp_parser.add_argument("new", help="path to the .eval log to compare against the baseline")
    cmp_parser.add_argument(
        "--json",
        action="store_true",
        help="emit the machine-readable JSON report instead of the text report",
    )
    cmp_parser.add_argument(
        "--output",
        "-o",
        metavar="PATH",
        help="write the report to PATH instead of stdout",
    )
    cmp_parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="list every changed sample rather than the first 10",
    )
    cmp_parser.add_argument(
        "--allow-positional-alignment",
        action="store_true",
        help=(
            "WEAK: if no stable sample key exists, match samples by their position in the "
            "sample list. Off by default. If the two runs ordered samples differently, this "
            "produces confident and wrong results, so every conclusion drawn from it is "
            "labelled unreliable."
        ),
    )
    cmp_parser.add_argument(
        "--exit-zero",
        action="store_true",
        help=(
            "always exit 0 when the comparison succeeds, even if differences were found. "
            "Does not suppress exit 2: a comparison that could not be performed stays an error."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    args = _parser().parse_args(argv)

    try:
        result = compare(
            args.old,
            args.new,
            allow_positional=args.allow_positional_alignment,
        )
    except LoadError as exc:
        print(f"inspect-replay: error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    text = to_json(result) if args.json else render(result, verbose=args.verbose)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as handle:
                handle.write(text + "\n")
        except OSError as exc:
            print(f"inspect-replay: error: could not write '{args.output}': {exc}", file=sys.stderr)
            return EXIT_ERROR
    else:
        print(text)

    if result.verdict is Verdict.NOT_COMPARABLE:
        print(
            "inspect-replay: error: the sample comparison could not be performed "
            f"({result.alignment_note})",
            file=sys.stderr,
        )
        return EXIT_ERROR
    if args.exit_zero:
        return EXIT_NO_DIFFERENCES
    return EXIT_DIFFERENCES if result.verdict is Verdict.CHANGED else EXIT_NO_DIFFERENCES


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
