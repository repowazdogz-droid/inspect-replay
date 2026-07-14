"""Orchestration: load, align, diff, and rank observations.

Two constraints are enforced here rather than left to the report writer.

**No causation.** inspect-replay reads two recorded artifacts. It cannot re-run
the evaluation, cannot hold one variable fixed while moving another, and cannot
observe the model provider. It therefore cannot establish that any configuration
change CAUSED any outcome change. What it can establish is that two things
changed together in the record. Every observation says exactly that, and a test
fails the build if causal vocabulary appears in generated output.

**No confident silence.** If the sample comparison could not be performed -- no
samples were logged, or nothing aligned -- the verdict is ``NOT_COMPARABLE``,
never ``UNCHANGED``. A tool that returns "unchanged" for an evaluation it never
looked at will pass a CI gate on a run that collapsed.
"""

from __future__ import annotations

from pathlib import Path

from inspect_ai.log import EvalLog

from .align import align_samples
from .config_diff import compare_config, compare_results, generation_fields_changed
from .loader import load_log
from .models import (
    AlignmentMethod,
    Comparison,
    FieldDiff,
    Observation,
    SampleDiff,
    SampleSummary,
    Verdict,
)
from .sample_diff import compare_reductions, compare_samples, summarize

__all__ = ["compare", "compare_logs"]


def compare(
    old_path: str | Path,
    new_path: str | Path,
    *,
    allow_positional: bool = False,
) -> Comparison:
    """Compare two Inspect logs by path. Read-only.

    Raises:
        LoadError: either path is not a readable Inspect log.
    """
    return compare_logs(
        load_log(old_path),
        load_log(new_path),
        allow_positional=allow_positional,
    )


def compare_logs(
    old: EvalLog,
    new: EvalLog,
    *,
    allow_positional: bool = False,
) -> Comparison:
    """Compare two already-loaded logs."""
    config = compare_config(old, new)
    results = compare_results(old, new)

    alignment = align_samples(old, new, allow_positional=allow_positional)
    warnings = list(alignment.warnings)

    # With epochs > 1 the evaluation's reported score comes from the REDUCED
    # per-sample score, not from individual epoch rows. Where both logs record
    # reductions, they are the comparison that matters.
    reduced = compare_reductions(old, new)
    if reduced is not None:
        samples = reduced
        summary = summarize(samples, old, new, alignment, reduced=True)
        alignment_note = (
            "aligned on sample id, comparing the epoch-reduced score that the evaluation "
            "reports (EvalLog.reductions)"
        )
        performed = True
        method = AlignmentMethod.SAMPLE_ID
    else:
        samples = compare_samples(alignment)
        summary = summarize(samples, old, new, alignment)
        alignment_note = alignment.note
        performed = alignment.performed
        method = alignment.method

    if summary.unit == "sample-epoch row":
        warnings.append(
            "epochs > 1 and no reductions were recorded, so the counts below are per "
            "sample-epoch row, not per dataset sample. A sample that flips in one epoch "
            "appears as one changed row; the evaluation's own score is computed from "
            "reduced scores, which are not present in these logs."
        )

    if old.status != "success" or new.status != "success":
        warnings.append(
            f"log status is '{old.status}' (old) and '{new.status}' (new); a run that did not "
            "complete successfully may have recorded only some of its samples"
        )

    changed = [d for d in (*config, *results) if d.differs]
    if not performed:
        verdict = Verdict.NOT_COMPARABLE
    elif changed or summary.differs:
        verdict = Verdict.CHANGED
    else:
        verdict = Verdict.UNCHANGED

    return Comparison(
        verdict=verdict,
        old_location=old.location or "<old>",
        new_location=new.location or "<new>",
        config=config,
        results=results,
        samples=samples,
        summary=summary,
        observations=_observations(config, results, summary, samples, method, performed),
        alignment=method,
        alignment_note=alignment_note,
        sample_comparison_performed=performed,
        warnings=tuple(warnings),
        old_status=old.status,
        new_status=new.status,
    )


def _find(diffs: tuple[FieldDiff, ...], name: str) -> FieldDiff | None:
    for d in diffs:
        if d.field == name:
            return d
    return None


def _temperature_nonzero(config: tuple[FieldDiff, ...]) -> bool:
    d = _find(config, "generate_config.temperature")
    if d is None:
        return False
    return any(isinstance(v, (int, float)) and v > 0 for v in (d.old, d.new))


def _observations(
    config: tuple[FieldDiff, ...],
    results: tuple[FieldDiff, ...],
    summary: SampleSummary,
    samples: tuple[SampleDiff, ...],
    method: AlignmentMethod,
    performed: bool,
) -> tuple[Observation, ...]:
    """Rank what changed alongside the observed result.

    Ranked by how directly the changed field bears on the recorded outcome, not
    by any measured effect size. This is an ordering of candidates for a human
    to investigate, not a causal attribution.
    """
    statements: list[tuple[str, tuple[str, ...]]] = []
    outcome_changed = summary.differs

    # --- things that invalidate the comparison come first ---
    if not performed:
        statements.append(
            (
                "The sample comparison could not be performed, so no statement about "
                "outcome changes can be made. Any configuration or headline-metric "
                "differences listed above are still real; the sample-level result is "
                "unknown, which is not the same as unchanged.",
                ("alignment.method",),
            )
        )

    if method is AlignmentMethod.POSITIONAL:
        statements.append(
            (
                "Samples were aligned by position, not by a stable id. If the two runs "
                "ordered samples differently, the sample-level findings below are wrong. "
                "Treat them as unreliable until alignment is confirmed.",
                ("alignment.method",),
            )
        )

    # Only a genuine change to the SAMPLE SET makes results incomparable. A
    # reshuffle or an epoch change does not, and saying so would put a false
    # statement at the top of the report.
    set_changed = [
        d
        for d in config
        if d.field in ("dataset.name", "dataset.location", "dataset.sample_ids") and d.differs
    ]
    if set_changed:
        statements.append(
            (
                "The dataset changed (" + ", ".join(d.field for d in set_changed) + "). The two "
                "runs did not evaluate the same sample set, so score totals are not comparable "
                "between them even where individual samples align.",
                tuple(e for d in set_changed for e in d.evidence),
            )
        )

    if summary.scores_not_comparable:
        statements.append(
            (
                f"{summary.scores_not_comparable} aligned {summary.unit}(s) share no scorer "
                "between the two runs, so their scores measure different quantities and are "
                "NOT compared. No regression or recovery can be reported for them, and the "
                "two runs' headline scores are not comparable to each other.",
                ("samples[].scores", "eval.scorers[].name"),
            )
        )

    # --- headline metrics ---
    metric_changes = [d for d in results if d.differs and d.field.startswith("results.")]
    headline = [d for d in metric_changes if not d.field.endswith(("_samples",))]
    if headline:
        # The metric names embed the (untrusted) scorer name, and the values are
        # untrusted, so neither is spliced into this authored sentence. Both are
        # shown, sanitised, in the Reported metrics section above.
        n = len(headline)
        statements.append(
            (
                f"{n} of the evaluation's own reported metric(s) changed (shown above under "
                "Reported metrics). These are the numbers the evaluation publishes, and they "
                "are recorded independently of the samples.",
                tuple(e for d in headline for e in d.evidence),
            )
        )

    # --- configuration, most directly outcome-bearing first ---
    scorer = _find(config, "scorer")
    if scorer is not None and scorer.differs:
        suffix = (
            " A scorer change alters how outcomes are computed from the same model output, "
            "so it is a possible contributor to every score difference in this report."
            if outcome_changed
            else " No sample score changed alongside it."
        )
        statements.append(("The scorer changed." + suffix, scorer.evidence))

    model = _find(config, "model.name")
    if model is not None and model.differs:
        # Model names are untrusted log content (an attacker can name a model to
        # inject wording), so they are not spliced here. The old and new names
        # are shown, sanitised, on the model.name line under Configuration.
        statements.append(
            (
                "The model changed (old and new names shown above under Configuration), "
                "alongside the outcomes below. Model output is generated remotely and is not "
                "reproducible from this log, so the size of its contribution cannot be "
                "determined from the recorded data.",
                model.evidence,
            )
        )

    prompt = _find(config, "prompt")
    if prompt is not None and prompt.differs:
        statements.append(
            (
                "The recorded prompt text changed (system message or prompt template). This "
                "changes the model input and is a possible contributor to the output changes "
                "below. Whether the two prompts are semantically equivalent cannot be "
                "determined from the recorded data.",
                prompt.evidence,
            )
        )

    tools = _find(config, "tools")
    if tools is not None and tools.differs:
        statements.append(
            (
                "The recorded tool set changed. Tools change what the model can do, and a "
                "tool change is a possible contributor to both output differences and new "
                "tool errors below.",
                tools.evidence,
            )
        )

    solver = [d for d in config if d.field.startswith(("solver.", "plan.")) and d.differs]
    if solver:
        statements.append(
            (
                "The solver configuration changed (" + ", ".join(d.field for d in solver) + ").",
                tuple(e for d in solver for e in d.evidence),
            )
        )

    gen = generation_fields_changed(config)
    if gen:
        # The parameter NAMES are a fixed, known set (temperature, top_p, ...),
        # so they are safe to list; the VALUES are untrusted and are shown,
        # sanitised, under Configuration rather than spliced in here.
        names = ", ".join(d.field.split(".", 1)[1] for d in gen)
        statements.append(
            (
                f"Generation parameters changed ({names}; values shown above under "
                "Configuration). These affect sampling from the model and are a possible "
                "contributor to any output or score change below.",
                tuple(e for d in gen for e in d.evidence),
            )
        )

    sandbox = _find(config, "sandbox")
    if sandbox is not None and sandbox.differs:
        statements.append(
            (
                "The sandbox configuration changed, which changes the environment tools "
                "execute in and is a possible contributor to any new tool errors below.",
                sandbox.evidence,
            )
        )

    # --- observed sample-level facts ---
    if summary.error_introduced:
        # Neither the error MESSAGES nor the sample KEYS are spliced in: both are
        # untrusted log content, and putting them into a sentence the tool is
        # asserting would risk terminal-control injection and let a crafted log
        # inject wording. The affected samples and their messages are shown,
        # sanitised, under Changed samples.
        statements.append(
            (
                f"{summary.error_introduced} {summary.unit}(s) errored in the new run that did "
                "not error in the old one (shown under Changed samples). An error suppresses a "
                "score, so their contribution to any aggregate metric changed regardless of "
                "model behaviour.",
                ("samples[].error",),
            )
        )

    if summary.mixed:
        statements.append(
            (
                f"{summary.mixed} {summary.unit}(s) had scorers move in opposite directions: "
                "at least one newly passing and at least one newly failing on the same sample. "
                "They are counted as neither a regression nor a recovery.",
                ("samples[].scores",),
            )
        )

    if summary.input_changed:
        statements.append(
            (
                # Wording avoids "because" -- not for meaning (this explains the
                # tool's own methodology, not an eval outcome) but because the
                # causal-language guard errs toward flagging, and the guarantee
                # is defined by that guard. Keep authored prose clear of its
                # trigger words.
                f"{summary.input_changed} {summary.unit}(s) kept their id but changed their "
                "recorded input, so they are no longer the same question. Their scores are "
                "NOT compared.",
                ("samples[].id", "samples[].input"),
            )
        )

    # --- the honest residual ---
    bearing = {"scorer", "model.name", "prompt", "tools", "solver.name", "solver.steps"}
    nothing_bears = not any(
        d.differs
        for d in config
        if d.field in bearing or d.field.startswith(("generate_config.", "dataset."))
    )
    if outcome_changed and nothing_bears:
        env = []
        if [d for d in config if d.field.startswith("packages.") and d.differs]:
            env.append("installed package versions changed")
        rev = _find(config, "revision.commit")
        if rev is not None and rev.differs:
            env.append("the recorded git commit changed")
        extra = (" " + " and ".join(env).capitalize() + ".") if env else ""
        temp_note = (
            " Generation temperature is above zero, so the two runs can differ in output "
            "from sampling alone, with no configuration difference at all."
            if _temperature_nonzero(config)
            else ""
        )
        statements.append(
            (
                "Sample outcomes changed but no recorded configuration field that bears on "
                "them changed. The source of this difference is not present in these logs. "
                "Candidates these logs would not capture: model-provider nondeterminism, a "
                "silently updated model served behind a stable name, and environment "
                "differences the log does not record." + extra + temp_note,
                ("eval", "samples[].scores"),
            )
        )

    return tuple(
        Observation(statement=s, evidence=tuple(dict.fromkeys(e)), rank=i + 1)
        for i, (s, e) in enumerate(statements)
    )
