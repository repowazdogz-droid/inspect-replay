"""Machine-readable output.

The schema is versioned (``schema_version``) and is ``0.x`` while the package is
pre-1.0: not yet frozen. Keys are sorted and values are plain JSON types, so the
same pair of logs always serialises to byte-identical output.

Contract vs presentation
-------------------------
Not every field is a stable contract. Build automation on the machine fields;
treat the prose fields as human-facing and liable to reword.

* **Machine contract** (safe to depend on within a schema version): ``verdict``,
  ``sample_comparison_performed``, every ``status`` / ``outcome`` / ``alignment``
  enum value, the numeric ``summary`` counters, ``config[].field`` names, and
  ``observations[].evidence`` / ``.rank`` / ``.relationship``.
* **Presentation, may change without a version bump**: ``observations[].
  statement`` and every ``note`` field are English prose for humans. Do not
  parse them. ``sample[].key`` is an OPAQUE identifier -- it has two internal
  shapes (``id::epoch`` for id alignment, ``sha256::epoch`` for hash alignment)
  and must be treated as a string token, not decomposed; use the separate
  ``sample_id`` and ``epoch`` fields instead.

Unknown information is represented as the string ``"UNKNOWN"`` or
``"NOT_CHECKED"`` in a status field -- never as ``false``, never as ``null``
standing in for "no difference", and never by omitting the key. A consumer that
filters on ``status == "SAME"`` must not accidentally pick up fields that were
never compared, and a consumer gating on ``newly_failing == 0`` must be able to
see that the comparison happened at all: ``verdict`` can be
``"NOT_COMPARABLE"``, and ``sample_comparison_performed`` is always present.
"""

from __future__ import annotations

import json
from typing import Any

from .models import SCHEMA_VERSION, Comparison, FieldDiff, SampleDiff

__all__ = ["to_dict", "to_json"]


def _field_diff(d: FieldDiff) -> dict[str, Any]:
    out: dict[str, Any] = {
        "field": d.field,
        "status": d.status.value,
        "old": d.old,
        "new": d.new,
        "evidence": list(d.evidence),
    }
    if d.note:
        out["note"] = d.note
    return out


def _sample_diff(d: SampleDiff) -> dict[str, Any]:
    out: dict[str, Any] = {
        "key": d.key,
        "sample_id": d.sample_id,
        "epoch": d.epoch,
        "outcome": d.outcome.value,
        "alignment": d.alignment.value,
        "alignment_is_weak": d.alignment_is_weak,
        "reduced": d.reduced,
        "input_changed": d.input_changed,
        "completion_changed": d.completion_changed.value,
        "metadata_changed": d.metadata_changed.value,
        "tool_calls": {"old": d.tool_calls_old, "new": d.tool_calls_new},
        "error": {"old": d.old_error, "new": d.new_error},
        "scores": [
            {
                "scorer": s.scorer,
                "old": {
                    "present": s.old_present,
                    "value": s.old_value,
                    "pass_fail": s.old_pass_fail.value,
                    "numeric": s.old_numeric,
                },
                "new": {
                    "present": s.new_present,
                    "value": s.new_value,
                    "pass_fail": s.new_pass_fail.value,
                    "numeric": s.new_numeric,
                },
                "comparable": s.comparable,
                "changed": s.changed,
            }
            for s in d.scores
        ],
    }
    if d.note:
        out["note"] = d.note
    return out


def to_dict(comparison: Comparison) -> dict[str, Any]:
    """Render a comparison as a plain, JSON-serialisable dict."""
    s = comparison.summary
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": "inspect-replay",
        "verdict": comparison.verdict.value,
        "sample_comparison_performed": comparison.sample_comparison_performed,
        "logs": {
            "old": {"location": comparison.old_location, "status": comparison.old_status},
            "new": {"location": comparison.new_location, "status": comparison.new_status},
        },
        "alignment": {
            "method": comparison.alignment.value,
            "is_weak": comparison.alignment.value == "POSITIONAL",
            "note": comparison.alignment_note,
        },
        "results": [_field_diff(d) for d in comparison.results],
        "results_changed": [_field_diff(d) for d in comparison.result_changes],
        "config": [_field_diff(d) for d in comparison.config],
        "config_changed": [_field_diff(d) for d in comparison.config_changes],
        "summary": {
            "unit": s.unit,
            "old_total_samples": s.old_total,
            "new_total_samples": s.new_total,
            "old_distinct_samples": s.old_distinct_samples,
            "new_distinct_samples": s.new_distinct_samples,
            "aligned": s.aligned,
            "scored_denominator": s.scored_denominator,
            "unchanged": s.unchanged,
            "newly_passing": s.newly_passing,
            "newly_failing": s.newly_failing,
            "score_changed": s.score_changed,
            "mixed": s.mixed,
            "error_introduced": s.error_introduced,
            "error_resolved": s.error_resolved,
            "errored_in_both": s.errored_in_both,
            "added": s.added,
            "removed": s.removed,
            "input_changed": s.input_changed,
            "scores_not_comparable": s.scores_not_comparable,
            "unknown": s.unknown,
            "unalignable_old": s.unalignable_old,
            "unalignable_new": s.unalignable_new,
        },
        "samples": [_sample_diff(d) for d in comparison.samples],
        "observations": [
            {
                "rank": o.rank,
                "statement": o.statement,
                "evidence": list(o.evidence),
                "relationship": "co-occurrence",
            }
            for o in comparison.observations
        ],
        "warnings": list(comparison.warnings),
    }


def to_json(comparison: Comparison, *, indent: int | None = 2) -> str:
    """Serialise a comparison deterministically.

    ``ensure_ascii=True`` so that every non-ASCII byte is escaped as ``\\uXXXX``.
    This is a security property, not just a portability one: with
    ``ensure_ascii=False``, ``json.dumps`` escapes only the C0 range (through
    ``\\u001f``, which includes ESC) but emits DEL (``0x7f``) and the C1 range
    (``0x80``-``0x9f``, including the C1 CSI ``0x9b``) as raw bytes. Those are
    terminal-active on some terminals, so a crafted log could otherwise inject
    them via the JSON path. Escaping everything closes it.
    """
    return json.dumps(to_dict(comparison), indent=indent, sort_keys=True, ensure_ascii=True)
