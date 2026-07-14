"""Regression tests for bugs found in hostile review.

Each test below corresponds to a defect that shipped in an internal version of
this tool and would have misled a user. They are kept as named tests, rather
than folded into the general suite, because each one encodes a specific way a
comparison tool can be confidently wrong.
"""

from __future__ import annotations

import copy

from conftest import make_chat_sample, make_log, make_reduced_log, make_sample, make_tool_log
from inspect_replay import compare_logs, input_fingerprint, render
from inspect_replay.cli import EXIT_ERROR
from inspect_replay.models import SampleOutcome, Status, Verdict

# --------------------------------------------------------------------------
# The chat-input fingerprint bug.
#
# ChatMessage.id defaults to a fresh uuid on every construction. Hashing a naive
# model dump of a chat input therefore gave two runs of the IDENTICAL dataset
# different fingerprints for every sample -- so every real regression was
# reported as "the input changed" and silently dropped from the counts.
# Chat-format inputs are the normal case for agentic and safety evals, so this
# defect quietly disabled the tool on exactly the evaluations it is for.
# --------------------------------------------------------------------------


def test_chat_message_ids_do_not_affect_the_fingerprint() -> None:
    a = make_chat_sample("q1", text="What is 2 + 2?", completion="4", value="C")
    b = make_chat_sample("q1", text="What is 2 + 2?", completion="5", value="I")

    assert a.input[0].id != b.input[0].id, "precondition: message ids differ"  # type: ignore[union-attr,index]
    assert input_fingerprint(a) == input_fingerprint(b), (
        "same question, different message uuids -- the fingerprint must not move"
    )


def test_chat_input_regression_is_reported_as_a_regression() -> None:
    old = make_log([make_chat_sample("q1", text="What is 2 + 2?", completion="4", value="C")])
    new = make_log([make_chat_sample("q1", text="What is 2 + 2?", completion="5", value="I")])
    result = compare_logs(old, new)

    assert result.summary.newly_failing == 1
    assert result.summary.input_changed == 0, "the question did not change; only the answer did"


def test_a_real_chat_input_change_is_still_detected() -> None:
    """The fix must not blind the input check -- only make it content-based."""
    old = make_log([make_chat_sample("q1", text="What is 2 + 2?", value="C")])
    new = make_log([make_chat_sample("q1", text="What is 9 * 9?", value="I")])
    assert compare_logs(old, new).summary.input_changed == 1


# --------------------------------------------------------------------------
# The silent-pass bug.
#
# When no sample could be aligned -- because a log was written with
# log_samples=False, or a run was truncated -- every counter was zero, so the
# verdict was UNCHANGED and the CLI exited 0. A CI gate would pass on an
# evaluation the tool had never looked at.
# --------------------------------------------------------------------------


def test_no_samples_is_not_comparable_not_unchanged(base_samples: list) -> None:
    old = make_log(base_samples)
    new = make_log([])  # log_samples=False
    result = compare_logs(old, new)

    assert result.verdict is Verdict.NOT_COMPARABLE
    assert result.verdict is not Verdict.UNCHANGED
    assert not result.sample_comparison_performed


def test_unalignable_samples_are_accounted_for_not_dropped(base_samples: list) -> None:
    """Every sample must land somewhere. Four samples in, four accounted for."""
    result = compare_logs(make_log(base_samples), make_log([]))
    s = result.summary
    assert s.old_total == 4
    assert s.unalignable_old == 4, "the old log's samples must not vanish from the accounting"
    assert s.added == 0 and s.removed == 0, "unalignable is not the same as added or removed"


def test_not_comparable_exits_with_an_error_code(tmp_path, base_samples: list) -> None:
    """The CI contract: never return 'no differences' for a comparison that did
    not happen."""
    from inspect_ai.log import write_eval_log

    from inspect_replay.cli import main

    old_path = tmp_path / "old.eval"
    new_path = tmp_path / "new.eval"
    write_eval_log(make_log(base_samples), str(old_path))
    write_eval_log(make_log([]), str(new_path))

    assert main(["compare", str(old_path), str(new_path)]) == EXIT_ERROR
    # --exit-zero must not launder it into a pass either.
    assert main(["compare", str(old_path), str(new_path), "--exit-zero"]) == EXIT_ERROR


def test_headline_metrics_are_compared_even_with_no_samples() -> None:
    """Metrics live in the log header, so they survive log_samples=False. This is
    the case where a samples-only tool reports 'unchanged' on a collapse."""
    old = make_log([], accuracy=0.92)
    new = make_log([], accuracy=0.21)
    result = compare_logs(old, new)

    changed = {d.field: (d.old, d.new) for d in result.result_changes}
    assert changed["results.exact_match.accuracy"] == (0.92, 0.21)
    assert result.verdict is Verdict.NOT_COMPARABLE, "samples still could not be compared"
    assert "0.92" in render(result) and "0.21" in render(result)


# --------------------------------------------------------------------------
# The tools falsehood.
#
# An earlier version printed "tools: NOT_CHECKED -- Inspect does not record the
# configured tool set". EvalSpec indeed has no tools field, but use_tools()
# records the tool set in plan.steps[].params["tools"], which IS in the header.
# The tool told users that a change they would care about -- a model being
# handed a shell -- was undetectable, while the data sat in the log it had open.
# --------------------------------------------------------------------------


def test_tool_set_change_is_detected(base_samples: list) -> None:
    old = make_tool_log(base_samples, tools=["add"])
    new = make_tool_log(base_samples, tools=["add", "exec_shell"])
    result = compare_logs(old, new)

    tools = next(d for d in result.config if d.field == "tools")
    assert tools.status is Status.CHANGED, "a new shell tool must be visible"
    assert tools.status is not Status.NOT_CHECKED
    assert "exec_shell" in str(tools.new)
    assert any("tool set changed" in o.statement for o in result.observations)


def test_identical_tool_sets_are_reported_as_same(base_samples: list) -> None:
    old = make_tool_log(base_samples, tools=["add"])
    new = make_tool_log(base_samples, tools=["add"])
    tools = next(d for d in compare_logs(old, new).config if d.field == "tools")
    assert tools.status is Status.SAME


def test_no_recorded_tools_is_unknown_not_a_claim_about_tools(baseline) -> None:
    """With no tool-bearing plan step, the honest answer is 'not recorded here',
    not 'unchanged' and not a claim that Inspect cannot record tools."""
    tools = next(
        d for d in compare_logs(baseline, copy.deepcopy(baseline)).config if d.field == "tools"
    )
    assert tools.status is Status.UNKNOWN
    assert tools.note is not None
    assert "does not record" not in tools.note


def test_a_changed_tool_set_renders_visibly(base_samples: list) -> None:
    """Truncating a long structured value to 60 chars once printed a CHANGED
    field as two identical strings."""
    old = make_tool_log(base_samples, tools=["add"])
    new = make_tool_log(base_samples, tools=["add", "exec_shell"])
    text = render(compare_logs(old, new))
    assert "exec_shell" in text, "the reader must be able to see what the tool change was"


# --------------------------------------------------------------------------
# Epochs and reductions.
#
# With epochs > 1, the score the evaluation reports is the epoch-REDUCED score.
# Comparing raw epoch rows and calling them samples reports regressions in a
# number no metric reads, against a denominator absent from the dataset.
# --------------------------------------------------------------------------


def test_reduced_scores_are_compared_not_raw_epoch_rows() -> None:
    """One sample, four epochs, a 'max' reducer. Epoch scores go [C,C,C,C] ->
    [C,I,I,I], but the reduced score stays C: the sample did not regress and the
    eval's accuracy does not move."""
    old = make_reduced_log(epoch_values=["C", "C", "C", "C"], reduced="C")
    new = make_reduced_log(epoch_values=["C", "I", "I", "I"], reduced="C")
    result = compare_logs(old, new)

    assert result.summary.newly_failing == 0, "the reduced score did not change"
    assert result.summary.unit == "sample"
    assert result.summary.aligned == 1, "one dataset sample, not four epoch rows"


def test_a_real_reduced_regression_is_still_caught() -> None:
    old = make_reduced_log(epoch_values=["C", "C"], reduced="C")
    new = make_reduced_log(epoch_values=["I", "I"], reduced="I")
    assert compare_logs(old, new).summary.newly_failing == 1


def test_epoch_rows_are_labelled_as_such_when_no_reductions_exist() -> None:
    """Without reductions the tool must still not call an epoch row a sample."""
    old = make_log([make_sample("q1", epoch=1, value="C"), make_sample("q1", epoch=2, value="C")])
    new = make_log([make_sample("q1", epoch=1, value="C"), make_sample("q1", epoch=2, value="I")])
    result = compare_logs(old, new)

    assert result.summary.unit == "sample-epoch row"
    assert result.summary.old_distinct_samples == 1
    assert any("sample-epoch row" in w for w in result.warnings)


# --------------------------------------------------------------------------
# Score readings that dropped real regressions.
# --------------------------------------------------------------------------


def test_correct_to_no_answer_is_a_regression() -> None:
    """A model that stops answering has regressed. Inspect's accuracy metric
    falls. Filing this under 'no pass/fail reading defined' hid it."""
    old = make_log([make_sample("q1", scores={"s": "C"})])
    new = make_log([make_sample("q1", scores={"s": "N"})])
    result = compare_logs(old, new)
    assert result.summary.newly_failing == 1
    assert result.samples[0].outcome is SampleOutcome.NEWLY_FAILING


def test_correct_to_partial_is_a_regression() -> None:
    old = make_log([make_sample("q1", scores={"s": "C"})])
    new = make_log([make_sample("q1", scores={"s": "P"})])
    assert compare_logs(old, new).summary.newly_failing == 1


def test_partial_to_incorrect_is_a_change_but_not_a_flip() -> None:
    """Both are 'not correct'. It is a real change, but nothing crossed the
    correct/not-correct boundary, so it is not a new failure."""
    old = make_log([make_sample("q1", scores={"s": "P"})])
    new = make_log([make_sample("q1", scores={"s": "I"})])
    result = compare_logs(old, new)
    assert result.summary.score_changed == 1
    assert result.summary.newly_failing == 0


def test_a_re_encoded_score_is_not_reported_as_a_change() -> None:
    """A scorer switching from "C" to 1.0 has not changed its verdict. Reporting
    a difference would be an artifact of the encoding."""
    old = make_log([make_sample("q1", scores={"s": "C"})])
    new = make_log([make_sample("q1", scores={"s": 1.0})])
    result = compare_logs(old, new)
    assert result.summary.unchanged == 1
    assert result.summary.score_changed == 0
    assert not result.samples_differ


def test_a_real_flip_across_an_encoding_change_is_still_caught() -> None:
    old = make_log([make_sample("q1", scores={"s": "C"})])
    new = make_log([make_sample("q1", scores={"s": 0.0})])
    assert compare_logs(old, new).summary.newly_failing == 1


# --------------------------------------------------------------------------
# Multi-scorer and both-errored samples.
# --------------------------------------------------------------------------


def test_scorers_moving_in_opposite_directions_are_reported_as_mixed() -> None:
    """Calling this sample a regression discards the scorer that improved, and
    the summary would then assert newly_passing == 0 when one newly passed."""
    old = make_log([make_sample("q1", scores={"safety": "C", "capability": "I"})])
    new = make_log([make_sample("q1", scores={"safety": "I", "capability": "C"})])
    result = compare_logs(old, new)

    assert result.summary.mixed == 1
    assert result.summary.newly_failing == 0
    assert result.summary.newly_passing == 0
    assert result.samples[0].outcome is SampleOutcome.MIXED


def test_errored_in_both_runs_is_not_counted_as_unchanged() -> None:
    """A sample that errored in both runs carries no score in either. Counting it
    as 'unchanged' inflates the denominator of any pass rate computed from the
    report."""
    old = make_log([make_sample("q1", error="Timeout")])
    new = make_log([make_sample("q1", error="Timeout")])
    result = compare_logs(old, new)

    assert result.summary.errored_in_both == 1
    assert result.summary.unchanged == 0
    assert result.summary.scored_denominator == 0, "no comparable score exists on either side"


def test_scored_denominator_excludes_everything_uncomparable() -> None:
    samples_old = [
        make_sample("ok", scores={"s": "C"}),
        make_sample("err", error="Timeout"),
        make_sample("moved", input="Q one", scores={"s": "C"}),
    ]
    samples_new = [
        make_sample("ok", scores={"s": "I"}),
        make_sample("err", error="Timeout"),
        make_sample("moved", input="Q two", scores={"s": "C"}),
    ]
    s = compare_logs(make_log(samples_old), make_log(samples_new)).summary
    assert s.aligned == 3
    assert s.scored_denominator == 1, "only 'ok' carries a comparable score on both sides"


# --------------------------------------------------------------------------
# Statements that were false.
# --------------------------------------------------------------------------


def test_a_shuffled_dataset_is_not_reported_as_a_changed_dataset(base_samples: list) -> None:
    """Comparing sample ids as an ordered list made a reshuffle look like a
    different dataset, and put 'the two runs did not evaluate the same sample
    set' at the top of the report. They did."""
    old = make_log(base_samples)
    new = make_log(list(reversed(copy.deepcopy(base_samples))))
    result = compare_logs(old, new)

    ids = next(d for d in result.config if d.field == "dataset.sample_ids")
    assert ids.status is Status.SAME
    assert not any(
        "did not evaluate the same sample set" in o.statement for o in result.observations
    )


def test_a_genuinely_different_sample_set_is_reported() -> None:
    old = make_log([make_sample("q1"), make_sample("q2", input="Q2?")])
    new = make_log([make_sample("q1"), make_sample("q3", input="Q3?")])
    result = compare_logs(old, new)

    ids = next(d for d in result.config if d.field == "dataset.sample_ids")
    assert ids.status is Status.CHANGED
    assert any("did not evaluate the same sample set" in o.statement for o in result.observations)


def test_recorded_empty_args_are_same_not_unknown(baseline) -> None:
    """task_args of {} was recorded and equals the other log's {}. Coercing an
    empty container to 'missing' reported a field as UNKNOWN that the log states
    plainly."""
    result = compare_logs(baseline, copy.deepcopy(baseline))
    for field in ("task.args", "model.args", "solver.args"):
        d = next(x for x in result.config if x.field == field)
        assert d.status is Status.SAME, f"{field} was recorded as {{}}; that is SAME, not UNKNOWN"


def test_system_message_and_prompt_template_are_not_conflated(base_samples: list) -> None:
    """Both solvers record their text under the param name 'template'. Taking the
    first match and calling it 'the system message' missed a prompt_template
    change entirely."""
    old = make_log(base_samples, system_message="SYS", prompt_template="Answer: {prompt}")
    new = make_log(
        base_samples, system_message="SYS", prompt_template="Think step by step: {prompt}"
    )
    result = compare_logs(old, new)

    prompt = next(d for d in result.config if d.field == "prompt")
    assert prompt.status is Status.CHANGED, "a prompt_template change must not be invisible"
    assert any("prompt text changed" in o.statement for o in result.observations)
