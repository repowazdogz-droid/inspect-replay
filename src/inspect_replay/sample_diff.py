"""Comparison of individual samples across two runs.

Two subtleties drive this module.

**What a score value licenses you to say.** Inspect score values may be
``"C"``/``"I"``/``"P"``/``"N"``, a bool, a number, a list, or a dict. Only some
carry a correctness reading. A numeric score moving from 0.4 to 0.7 has NOT
"newly passed" -- the log records no threshold saying what passing means, and
inventing one would manufacture regressions the data does not support. But
``"C"`` to ``"N"`` (correct, then refusing to answer) IS a real regression:
Inspect's own accuracy metric falls, and filing it under "no pass/fail reading"
would hide it.

**What the evaluation actually reports.** With ``epochs > 1`` a dataset sample
produces several rows, and the score the evaluation reports is the REDUCED score
across those epochs (``EvalLog.reductions``). Comparing raw epoch rows and
calling them samples reports regressions in a number nobody reads, against a
denominator that does not exist in the dataset. Where reductions are recorded,
they are what gets compared.
"""

from __future__ import annotations

from typing import Any

from inspect_ai.log import EvalLog, EvalSample
from inspect_ai.scorer import CORRECT, INCORRECT, NOANSWER, PARTIAL

from .align import Alignment, input_fingerprint
from .models import (
    AlignmentMethod,
    PassFail,
    SampleDiff,
    SampleOutcome,
    SampleSummary,
    ScoreDiff,
    Status,
)

__all__ = [
    "compare_reductions",
    "compare_samples",
    "score_numeric",
    "score_pass_fail",
    "summarize",
]

_STR_READING = {
    CORRECT: PassFail.PASS,
    INCORRECT: PassFail.FAIL,
    PARTIAL: PassFail.PARTIAL,
    NOANSWER: PassFail.NO_ANSWER,
}

_STR_NUMERIC = {CORRECT: 1.0, INCORRECT: 0.0, PARTIAL: 0.5, NOANSWER: 0.0}


def score_pass_fail(value: Any) -> PassFail:
    """Correctness reading of a score value, or UNKNOWN where none is defined.

    Deliberately conservative. Only the value types Inspect gives a correctness
    meaning are read:

    * ``"C"`` / ``"I"`` / ``"P"`` / ``"N"`` -- Inspect's own score constants
    * ``True`` / ``False``
    * exactly ``1`` or ``0`` -- Inspect's numeric encoding of correct/incorrect

    Every other numeric value is UNKNOWN, because the log records no threshold
    that would make 0.7 a pass. Lists and dicts are UNKNOWN for the same reason.
    """
    if isinstance(value, bool):
        return PassFail.PASS if value else PassFail.FAIL
    if isinstance(value, str):
        return _STR_READING.get(value, PassFail.UNKNOWN)
    if isinstance(value, (int, float)):
        if value == 1:
            return PassFail.PASS
        if value == 0:
            return PassFail.FAIL
        return PassFail.UNKNOWN
    return PassFail.UNKNOWN


def score_numeric(value: Any) -> float | None:
    """Magnitude of a score, where it has one.

    Matches Inspect's own ``value_to_float`` for the categorical values
    (C=1.0, P=0.5, I=0.0, N=0.0) so that a degradation to PARTIAL is visible as
    the loss it is.
    """
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value in _STR_NUMERIC:
        return _STR_NUMERIC[value]
    return None


def _tool_calls(sample: EvalSample) -> int | None:
    """Count recorded tool calls, or None if events were not recorded.

    A tool-call count is a behavioural observation, not a configuration fact: it
    says the model used tools differently, not that the tools were configured
    differently.
    """
    events = getattr(sample, "events", None)
    if not events:
        return None
    return sum(1 for e in events if getattr(e, "event", None) == "tool")


def _completion(sample: EvalSample) -> str | None:
    out = getattr(sample, "output", None)
    completion = getattr(out, "completion", None) if out is not None else None
    return completion if isinstance(completion, str) else None


def _error_message(sample: EvalSample) -> str | None:
    err = getattr(sample, "error", None)
    return err.message if err is not None else None


def _score_diff(scorer: str, old: Any, new: Any, old_present: bool, new_present: bool) -> ScoreDiff:
    return ScoreDiff(
        scorer=scorer,
        old_value=old,
        new_value=new,
        old_pass_fail=score_pass_fail(old) if old_present else PassFail.UNKNOWN,
        new_pass_fail=score_pass_fail(new) if new_present else PassFail.UNKNOWN,
        old_numeric=score_numeric(old) if old_present else None,
        new_numeric=score_numeric(new) if new_present else None,
        old_present=old_present,
        new_present=new_present,
    )


def _score_diffs(old: EvalSample, new: EvalSample) -> tuple[ScoreDiff, ...]:
    old_scores = old.scores or {}
    new_scores = new.scores or {}
    return tuple(
        _score_diff(
            name,
            old_scores[name].value if name in old_scores else None,
            new_scores[name].value if name in new_scores else None,
            name in old_scores,
            name in new_scores,
        )
        for name in sorted(set(old_scores) | set(new_scores))
    )


def _verdict_for(score: ScoreDiff) -> SampleOutcome | None:
    """The outcome one scorer's movement implies, or None if it did not move.

    A crossing of the correct / not-correct boundary is a regression or a
    recovery. ``"C"`` to ``"P"`` counts as a regression: the answer is no longer
    correct and Inspect's accuracy metric drops accordingly. A move within the
    not-correct band (``"I"`` to ``"P"``) is a change but not a flip.
    """
    if not score.changed or score.equivalent:
        return None
    if score.graded:
        was_correct = score.old_pass_fail is PassFail.PASS
        is_correct = score.new_pass_fail is PassFail.PASS
        if was_correct and not is_correct:
            return SampleOutcome.NEWLY_FAILING
        if is_correct and not was_correct:
            return SampleOutcome.NEWLY_PASSING
    return SampleOutcome.SCORE_CHANGED


def _classify(
    old_error: str | None,
    new_error: str | None,
    scores: tuple[ScoreDiff, ...],
    input_changed: bool,
) -> tuple[SampleOutcome, str | None]:
    """Decide one aligned sample's outcome. Order of checks matters."""
    # An input change breaks the premise of comparison: these are not the same
    # question. Do not compare their scores at all.
    if input_changed:
        return (
            SampleOutcome.INPUT_CHANGED,
            "the sample id matched but the recorded input differs, so these are not the "
            "same question and their scores are not compared",
        )

    if new_error and not old_error:
        return SampleOutcome.ERROR_INTRODUCED, None
    if old_error and not new_error:
        return SampleOutcome.ERROR_RESOLVED, None
    if old_error and new_error:
        return (
            SampleOutcome.ERRORED_IN_BOTH,
            "errored in both runs; no score exists on either side, so this sample "
            "contributes nothing to a pass rate in either",
        )

    if not scores:
        return SampleOutcome.UNKNOWN, "no scores recorded on either side"

    # Only scorers present on BOTH sides measure a comparable quantity. A score
    # from a scorer that ran in one run only has nothing to be compared against,
    # and pretending otherwise manufactures differences.
    shared = [s for s in scores if s.comparable]
    if not shared:
        old_names = sorted(s.scorer for s in scores if s.old_present)
        new_names = sorted(s.scorer for s in scores if s.new_present)
        return (
            SampleOutcome.SCORES_NOT_COMPARABLE,
            f"the two runs share no scorer (old: {', '.join(old_names) or 'none'}; "
            f"new: {', '.join(new_names) or 'none'}), so there is no common quantity to "
            "compare and no regression or recovery can be reported for this sample",
        )

    verdicts = [v for v in (_verdict_for(s) for s in shared) if v is not None]
    if not verdicts:
        # Includes the case where a scorer changed its encoding ("C" -> 1.0)
        # without changing its verdict.
        return SampleOutcome.UNCHANGED, None

    failing = SampleOutcome.NEWLY_FAILING in verdicts
    passing = SampleOutcome.NEWLY_PASSING in verdicts
    if failing and passing:
        return (
            SampleOutcome.MIXED,
            "scorers moved in opposite directions on this sample: at least one newly "
            "passing and at least one newly failing. It is counted as neither",
        )
    if failing:
        return SampleOutcome.NEWLY_FAILING, None
    if passing:
        return SampleOutcome.NEWLY_PASSING, None

    graded = all(s.graded for s in shared if s.changed)
    return (
        SampleOutcome.SCORE_CHANGED,
        None
        if graded
        else (
            "the score value changed but no correctness reading is defined for it, so this "
            "is not reported as a regression or a recovery"
        ),
    )


def _diff_pair(key: str, old: EvalSample, new: EvalSample, method: AlignmentMethod) -> SampleDiff:
    input_changed = input_fingerprint(old) != input_fingerprint(new)
    scores = _score_diffs(old, new)
    old_error, new_error = _error_message(old), _error_message(new)
    outcome, note = _classify(old_error, new_error, scores, input_changed)

    old_completion, new_completion = _completion(old), _completion(new)
    if old_completion is None or new_completion is None:
        completion_status = Status.UNKNOWN
    else:
        completion_status = Status.SAME if old_completion == new_completion else Status.CHANGED

    old_meta, new_meta = old.metadata or {}, new.metadata or {}
    if not old_meta and not new_meta:
        metadata_status = Status.UNKNOWN
    else:
        metadata_status = Status.SAME if old_meta == new_meta else Status.CHANGED

    return SampleDiff(
        key=key,
        sample_id=new.id,
        epoch=new.epoch,
        outcome=outcome,
        alignment=method,
        scores=scores,
        old_error=old_error,
        new_error=new_error,
        input_changed=input_changed,
        completion_changed=completion_status,
        tool_calls_old=_tool_calls(old),
        tool_calls_new=_tool_calls(new),
        metadata_changed=metadata_status,
        note=note,
    )


def compare_samples(alignment: Alignment) -> tuple[SampleDiff, ...]:
    """Compare every aligned sample and account for every unaligned one.

    Output is sorted by key, so the same pair of logs always produces the same
    ordering.
    """
    diffs = [_diff_pair(k, *alignment.pairs[k], alignment.method) for k in sorted(alignment.pairs)]

    # When no alignment was possible, samples on each side are unalignable, not
    # deleted and added. summarize() keeps them out of the added/removed counts.
    for key in sorted(alignment.old_only):
        s = alignment.old_only[key]
        diffs.append(
            SampleDiff(
                key=key,
                sample_id=s.id,
                epoch=s.epoch,
                outcome=SampleOutcome.REMOVED,
                alignment=alignment.method,
                old_error=_error_message(s),
                note="present in the old log, absent from the new one",
            )
        )
    for key in sorted(alignment.new_only):
        s = alignment.new_only[key]
        diffs.append(
            SampleDiff(
                key=key,
                sample_id=s.id,
                epoch=s.epoch,
                outcome=SampleOutcome.ADDED,
                alignment=alignment.method,
                new_error=_error_message(s),
                note="present in the new log, absent from the old one",
            )
        )

    return tuple(diffs)


def compare_reductions(old: EvalLog, new: EvalLog) -> tuple[SampleDiff, ...] | None:
    """Compare per-sample REDUCED scores, where both logs record them.

    With ``epochs > 1``, Inspect reduces each sample's several epoch scores to
    one value, and that reduced value is what the evaluation's headline metric
    is computed from. Comparing raw epoch rows instead would report regressions
    in numbers that no metric reads: a sample scored ``[C,C,C,C]`` then
    ``[C,I,I,I]`` under a ``max`` reducer is still correct, and the eval's
    accuracy does not move.

    Returns ``None`` when either log records no reductions, in which case the
    caller falls back to per-epoch comparison.
    """
    if not old.reductions or not new.reductions:
        return None

    def by_sample(log: EvalLog) -> dict[str | int, dict[str, Any]]:
        out: dict[str | int, dict[str, Any]] = {}
        for reduction in log.reductions or []:
            scorer = reduction.scorer or reduction.reducer or "score"
            for s in reduction.samples:
                if s.sample_id is None:
                    continue
                out.setdefault(s.sample_id, {})[scorer] = s.value
        return out

    old_by, new_by = by_sample(old), by_sample(new)
    old_inputs = {s.id: input_fingerprint(s) for s in old.samples or []}
    new_inputs = {s.id: input_fingerprint(s) for s in new.samples or []}
    old_errors = {s.id: _error_message(s) for s in old.samples or []}
    new_errors = {s.id: _error_message(s) for s in new.samples or []}

    diffs: list[SampleDiff] = []
    for sample_id in sorted(set(old_by) & set(new_by), key=str):
        o, n = old_by[sample_id], new_by[sample_id]
        scores = tuple(
            _score_diff(name, o.get(name), n.get(name), name in o, name in n)
            for name in sorted(set(o) | set(n))
        )
        input_changed = (
            sample_id in old_inputs
            and sample_id in new_inputs
            and old_inputs[sample_id] != new_inputs[sample_id]
        )
        # An error in ANY epoch is not the same as the reduced sample erroring;
        # only treat the sample as errored when no reduced score exists.
        outcome, note = _classify(None, None, scores, input_changed)
        diffs.append(
            SampleDiff(
                key=str(sample_id),
                sample_id=sample_id,
                epoch=None,
                outcome=outcome,
                alignment=AlignmentMethod.SAMPLE_ID,
                scores=scores,
                old_error=old_errors.get(sample_id),
                new_error=new_errors.get(sample_id),
                input_changed=input_changed,
                reduced=True,
                note=note,
            )
        )

    for sample_id in sorted(set(old_by) - set(new_by), key=str):
        diffs.append(
            SampleDiff(
                key=str(sample_id),
                sample_id=sample_id,
                epoch=None,
                outcome=SampleOutcome.REMOVED,
                alignment=AlignmentMethod.SAMPLE_ID,
                reduced=True,
                note="present in the old log, absent from the new one",
            )
        )
    for sample_id in sorted(set(new_by) - set(old_by), key=str):
        diffs.append(
            SampleDiff(
                key=str(sample_id),
                sample_id=sample_id,
                epoch=None,
                outcome=SampleOutcome.ADDED,
                alignment=AlignmentMethod.SAMPLE_ID,
                reduced=True,
                note="present in the new log, absent from the old one",
            )
        )
    return tuple(diffs)


def summarize(
    diffs: tuple[SampleDiff, ...],
    old: EvalLog,
    new: EvalLog,
    alignment: Alignment,
    *,
    reduced: bool = False,
) -> SampleSummary:
    """Count outcomes, keeping denominators and the unit of analysis visible."""
    counts: dict[SampleOutcome, int] = dict.fromkeys(SampleOutcome, 0)
    for d in diffs:
        counts[d.outcome] += 1

    old_samples = old.samples or []
    new_samples = new.samples or []
    aligned = sum(1 for d in diffs if d.outcome not in (SampleOutcome.ADDED, SampleOutcome.REMOVED))

    multi_epoch = any(s.epoch > 1 for s in (*old_samples, *new_samples))
    unit = "sample" if reduced or not multi_epoch else "sample-epoch row"

    performed = alignment.performed or reduced
    return SampleSummary(
        old_total=len(old_samples),
        new_total=len(new_samples),
        aligned=aligned if performed else 0,
        unit=unit,
        old_distinct_samples=len({s.id for s in old_samples}),
        new_distinct_samples=len({s.id for s in new_samples}),
        unchanged=counts[SampleOutcome.UNCHANGED],
        newly_passing=counts[SampleOutcome.NEWLY_PASSING],
        newly_failing=counts[SampleOutcome.NEWLY_FAILING],
        score_changed=counts[SampleOutcome.SCORE_CHANGED],
        mixed=counts[SampleOutcome.MIXED],
        error_introduced=counts[SampleOutcome.ERROR_INTRODUCED],
        error_resolved=counts[SampleOutcome.ERROR_RESOLVED],
        errored_in_both=counts[SampleOutcome.ERRORED_IN_BOTH],
        # With no alignment, every sample lands in old_only/new_only. Those are
        # unalignable, not added and removed -- do not launder them into counts
        # that imply the dataset changed.
        added=counts[SampleOutcome.ADDED] if performed else 0,
        removed=counts[SampleOutcome.REMOVED] if performed else 0,
        input_changed=counts[SampleOutcome.INPUT_CHANGED],
        scores_not_comparable=counts[SampleOutcome.SCORES_NOT_COMPARABLE],
        unknown=counts[SampleOutcome.UNKNOWN],
        unalignable_old=0 if performed else len(old_samples),
        unalignable_new=0 if performed else len(new_samples),
    )
