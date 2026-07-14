"""Human-readable terminal report.

Plain text, no colour: the output is meant to be readable in a terminal, pasted
into an issue, and diffed in CI without escape codes getting in the way.

One rendering rule earns its complexity. Truncating a long structured value to
a fixed width can print a CHANGED field as two identical strings -- a tool whose
entire job is showing what changed, showing a change as a non-change. So
structured values are rendered by their DIFFERING parts, not by their first
sixty characters.
"""

from __future__ import annotations

from typing import Any

from .models import (
    Comparison,
    FieldDiff,
    SampleDiff,
    SampleOutcome,
    Status,
    Verdict,
)
from .text import sanitize

__all__ = ["render"]

_OUTCOME_LABEL = {
    SampleOutcome.NEWLY_FAILING: "newly failing",
    SampleOutcome.NEWLY_PASSING: "newly passing",
    SampleOutcome.SCORE_CHANGED: "score changed (not a pass/fail flip)",
    SampleOutcome.MIXED: "mixed (scorers moved in opposite directions)",
    SampleOutcome.ERROR_INTRODUCED: "error introduced",
    SampleOutcome.ERROR_RESOLVED: "error resolved",
    SampleOutcome.ERRORED_IN_BOTH: "errored in both runs",
    SampleOutcome.ADDED: "added",
    SampleOutcome.REMOVED: "removed",
    SampleOutcome.INPUT_CHANGED: "input changed (scores not compared)",
    SampleOutcome.SCORES_NOT_COMPARABLE: "scores not comparable (no shared scorer)",
    SampleOutcome.UNKNOWN: "outcome unknown",
    SampleOutcome.UNCHANGED: "unchanged",
}

_NOTEWORTHY = (
    SampleOutcome.NEWLY_FAILING,
    SampleOutcome.NEWLY_PASSING,
    SampleOutcome.MIXED,
    SampleOutcome.ERROR_INTRODUCED,
    SampleOutcome.ERROR_RESOLVED,
    SampleOutcome.SCORE_CHANGED,
    SampleOutcome.INPUT_CHANGED,
    SampleOutcome.SCORES_NOT_COMPARABLE,
    SampleOutcome.ADDED,
    SampleOutcome.REMOVED,
)


def _fmt(value: object, limit: int = 60) -> str:
    if value is None:
        return "<not recorded>"
    # sanitize before measuring/truncating: values come from the untrusted log
    # and may contain terminal control sequences. " ".split() drops the
    # whitespace controls; sanitize() neutralises the rest (ANSI/OSC escapes).
    text = sanitize(" ".join(str(value).split()))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _divergences(old: Any, new: Any, path: str = "") -> list[tuple[str, Any, Any]]:
    """Locate where two structured values actually differ.

    Returns (path, old, new) triples for the leaves that differ, so a change
    buried inside a long dict or list is shown as the change it is, rather than
    as two identically-truncated blobs.
    """
    if isinstance(old, dict) and isinstance(new, dict):
        out: list[tuple[str, Any, Any]] = []
        for key in sorted(set(old) | set(new)):
            sub = f"{path}.{key}" if path else str(key)
            if key not in old:
                out.append((sub, None, new[key]))
            elif key not in new:
                out.append((sub, old[key], None))
            elif old[key] != new[key]:
                out.extend(_divergences(old[key], new[key], sub))
        return out
    if isinstance(old, list) and isinstance(new, list):
        out = []
        if len(old) == len(new):
            for i, (o, n) in enumerate(zip(old, new, strict=True)):
                if o != n:
                    out.extend(_divergences(o, n, f"{path}[{i}]"))
            return out
        # Lists of different lengths: report what entered and what left, rather
        # than truncating two long lists to a prefix they happen to share. A
        # tool set gaining a shell must not render as an elided blob.
        added = [x for x in new if x not in old]
        removed = [x for x in old if x not in new]
        if added:
            out.append((f"{path} +added", None, added))
        if removed:
            out.append((f"{path} -removed", removed, None))
        return out or [(path, old, new)]
    return [(path, old, new)]


def _label(token: str) -> str:
    """A field name, dotted path, sample key, or scorer name for display.

    These read like structural identifiers, but they embed untrusted substrings
    -- a package name, a tool name, a scorer name, a sample id -- so they are
    sanitised for control characters exactly like values are.
    """
    return sanitize(token)


def _field_lines(d: FieldDiff) -> list[str]:
    """Render one changed configuration field."""
    field = _label(d.field)
    if d.status is Status.ADDED:
        return [f"- {field}: <not recorded in old> → {_fmt(d.new)}"]
    if d.status is Status.REMOVED:
        return [f"- {field}: {_fmt(d.old)} → <not recorded in new>"]

    if isinstance(d.old, (dict, list)) or isinstance(d.new, (dict, list)):
        parts = _divergences(d.old, d.new)
        if len(parts) <= 6 and all(p for p, _, _ in parts):
            lines = [f"- {field}: changed in {len(parts)} place(s)"]
            for path, o, n in parts:
                if path.endswith(" +added"):
                    lines.append(f"    {_label(path.removesuffix(' +added'))}: added {_fmt(n, 70)}")
                elif path.endswith(" -removed"):
                    lines.append(
                        f"    {_label(path.removesuffix(' -removed'))}: removed {_fmt(o, 70)}"
                    )
                else:
                    lines.append(f"    {_label(path)}: {_fmt(o, 70)} → {_fmt(n, 70)}")
            return lines
    return [f"- {field}: {_fmt(d.old)} → {_fmt(d.new)}"]


def _sample_line(d: SampleDiff) -> str:
    bits = [f"  {_label(d.key)}: {_OUTCOME_LABEL[d.outcome]}"]

    if d.outcome is SampleOutcome.SCORES_NOT_COMPARABLE:
        old = ", ".join(
            f"{_label(s.scorer)}={_fmt(s.old_value, 20)}" for s in d.scores if s.old_present
        )
        new = ", ".join(
            f"{_label(s.scorer)}={_fmt(s.new_value, 20)}" for s in d.scores if s.new_present
        )
        bits.append(f"      old: {old or '<none>'}")
        bits.append(f"      new: {new or '<none>'}")
        return "\n".join(bits)

    for s in d.scores:
        if s.changed:
            bits.append(
                f"      {_label(s.scorer)}: {_fmt(s.old_value, 30)} → {_fmt(s.new_value, 30)}"
            )

    if d.outcome is SampleOutcome.ERROR_INTRODUCED and d.new_error:
        bits.append(f"      error: {_fmt(d.new_error, 90)}")
    if d.outcome is SampleOutcome.ERROR_RESOLVED and d.old_error:
        bits.append(f"      was: {_fmt(d.old_error, 90)}")
    if d.outcome is SampleOutcome.ERRORED_IN_BOTH:
        bits.append(f"      old error: {_fmt(d.old_error, 70)}")
        bits.append(f"      new error: {_fmt(d.new_error, 70)}")

    # A sample whose metadata moved underneath it is not a like-for-like
    # comparison, and the reader must be able to see that from the report.
    if d.metadata_changed is Status.CHANGED:
        bits.append("      note: sample metadata also changed between the runs")
    if (
        d.tool_calls_old is not None
        and d.tool_calls_new is not None
        and d.tool_calls_old != d.tool_calls_new
    ):
        bits.append(f"      tool calls: {d.tool_calls_old} → {d.tool_calls_new}")
    return "\n".join(bits)


def render(comparison: Comparison, *, verbose: bool = False) -> str:
    """Render a comparison as plain text."""
    lines: list[str] = []
    s = comparison.summary
    unit = s.unit

    lines.append(f"Evaluation: {comparison.verdict.value}")
    # Locations usually come from the invoking argument, but a log can carry its
    # own 'location' field, so treat them as untrusted too.
    lines.append(f"  old: {sanitize(comparison.old_location)}")
    lines.append(f"  new: {sanitize(comparison.new_location)}")
    if comparison.verdict is Verdict.NOT_COMPARABLE:
        lines.append("  the sample comparison could not be performed; see Samples below")
    lines.append("")

    # --- headline metrics ---
    result_changes = comparison.result_changes
    lines.append("Reported metrics:")
    if result_changes:
        for d in result_changes:
            lines.extend(_field_lines(d))
    else:
        recorded = [d for d in comparison.results if d.status is Status.SAME]
        lines.append(
            f"- {len(recorded)} metric(s) unchanged"
            if recorded
            else "- no metrics recorded in either log"
        )
    lines.append("")

    # --- configuration ---
    lines.append("Configuration:")
    changes = comparison.config_changes
    if changes:
        for d in changes:
            lines.extend(_field_lines(d))
            if d.note:
                lines.append(f"    note: {sanitize(d.note)}")
    else:
        lines.append("- no recorded configuration field changed")

    same = sum(1 for d in comparison.config if d.status is Status.SAME)
    unknown = [d for d in comparison.config if d.status is Status.UNKNOWN]
    lines.append(f"- {same} other field(s): unchanged")
    if unknown:
        lines.append(
            f"- {len(unknown)} field(s): UNKNOWN, not recorded in at least one log "
            "(not the same as unchanged): "
            + ", ".join(_label(d.field) for d in unknown[:6])
            + ("…" if len(unknown) > 6 else "")
        )
    lines.append("")

    # --- samples ---
    lines.append(f"Samples (unit: {unit}):")
    lines.append(f"- {s.old_total} in old log, {s.new_total} in new log")
    if not comparison.sample_comparison_performed:
        lines.append(f"- 0 aligned: {sanitize(comparison.alignment_note)}")
        lines.append(
            f"- {s.unalignable_old + s.unalignable_new} could not be aligned "
            f"({s.unalignable_old} from old, {s.unalignable_new} from new)"
        )
        lines.append("- NO sample-level conclusion can be drawn from these logs")
    else:
        lines.append(f"- {s.aligned} aligned ({sanitize(comparison.alignment_note)})")
        lines.append(f"- {s.unchanged} unchanged")
        for label, count in (
            ("newly failing", s.newly_failing),
            ("newly passing", s.newly_passing),
            ("mixed (scorers moved in opposite directions)", s.mixed),
            ("score changed, but not across the pass/fail boundary", s.score_changed),
            ("errors introduced", s.error_introduced),
            ("errors resolved", s.error_resolved),
            ("errored in both runs (no score on either side)", s.errored_in_both),
            ("added", s.added),
            ("removed", s.removed),
            ("id matched but input changed (scores not compared)", s.input_changed),
            ("scores not comparable: the two runs share no scorer", s.scores_not_comparable),
            ("outcome unknown", s.unknown),
        ):
            if count:
                lines.append(f"- {count} {label}")
        lines.append(
            f"- {s.scored_denominator} carry a comparable score on both sides "
            "(the only denominator a pass rate may be computed over)"
        )
    lines.append("")

    noteworthy = [sd for sd in comparison.samples if sd.outcome in _NOTEWORTHY]
    if noteworthy:
        shown = noteworthy if verbose else noteworthy[:10]
        lines.append("Changed samples:")
        for sd in shown:
            lines.append(_sample_line(sd))
        if len(noteworthy) > len(shown):
            lines.append(f"  … and {len(noteworthy) - len(shown)} more (use --verbose to see all)")
        lines.append("")

    if comparison.observations:
        lines.append("What changed alongside the result (ranked; co-occurrence, not causation):")
        for o in comparison.observations:
            # Statements are authored by the tool and no longer splice untrusted
            # values, but sanitise at the boundary anyway -- defence in depth.
            lines.append(f"{o.rank}. {sanitize(o.statement)}")
            lines.append(f"     evidence: {sanitize(', '.join(o.evidence))}")
        lines.append("")

    if comparison.warnings:
        lines.append("Warnings:")
        # Warnings are authored, but one embeds the log status; sanitise anyway.
        lines.extend(f"- {sanitize(w)}" for w in comparison.warnings)
        lines.append("")

    lines.append(
        "inspect-replay compares recorded artifacts. It does not re-run the evaluation; it "
        "reports what changed together, not why the result differs. See "
        "docs/assurance-boundary.md."
    )
    return "\n".join(lines)
