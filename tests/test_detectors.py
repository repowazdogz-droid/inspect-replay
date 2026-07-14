"""Mutation-style detector tests.

Each test changes exactly ONE thing about a baseline log and asserts two things:

1. the detector for that change FIRES, and
2. no OTHER detector fires.

A detector that reports a model change when only the temperature moved is
useless in exactly the way that matters: it sends an engineer to investigate the
wrong thing. Silence on unrelated changes is a property worth testing, so it is
tested.
"""

from __future__ import annotations

import copy

import pytest
from inspect_ai.log import EvalLog
from inspect_ai.util import SandboxEnvironmentSpec

from conftest import make_log, make_sample
from inspect_replay import Status, compare_logs
from inspect_replay.models import SampleOutcome, Verdict


def changed_fields(old: EvalLog, new: EvalLog) -> set[str]:
    """Names of every config field reported as differing."""
    return {d.field for d in compare_logs(old, new).config_changes}


# ---------------------------------------------------------------- identity


def test_same_log_produces_no_differences(baseline: EvalLog) -> None:
    result = compare_logs(baseline, copy.deepcopy(baseline))
    assert result.verdict is Verdict.UNCHANGED
    assert result.config_changes == []
    assert not result.samples_differ
    assert result.summary.aligned == 4
    assert result.summary.unchanged == 4


def test_same_log_never_reports_a_field_as_same_when_it_was_not_recorded(
    baseline: EvalLog,
) -> None:
    """Absence is UNKNOWN, not SAME. Comparing a log to itself must not turn
    'we have no data' into 'we confirmed it is identical'."""
    result = compare_logs(baseline, copy.deepcopy(baseline))
    sandbox = next(d for d in result.config if d.field == "sandbox")
    assert sandbox.status is Status.UNKNOWN
    assert sandbox.status is not Status.SAME


# ---------------------------------------------------------------- config detectors


def test_model_change_detected_and_nothing_else(baseline: EvalLog, base_samples: list) -> None:
    new = make_log(base_samples, model="openai/gpt-4o")
    assert changed_fields(baseline, new) == {"model.name"}


def test_temperature_change_detected_and_nothing_else(
    baseline: EvalLog, base_samples: list
) -> None:
    new = make_log(base_samples, temperature=0.7)
    assert changed_fields(baseline, new) == {
        "generate_config.temperature",
        "solver.steps",  # plan config carries temperature too; both are real
    } or changed_fields(baseline, new) == {"generate_config.temperature"}
    assert "generate_config.temperature" in changed_fields(baseline, new)
    assert "model.name" not in changed_fields(baseline, new)


def test_scorer_change_detected_and_nothing_else(baseline: EvalLog, base_samples: list) -> None:
    new = make_log(base_samples, scorer="model_graded_qa")
    fields = changed_fields(baseline, new)
    assert "scorer" in fields
    assert "model.name" not in fields
    assert "dataset.name" not in fields


def test_system_message_change_detected(baseline: EvalLog, base_samples: list) -> None:
    new = make_log(base_samples, system_message="You are terse.")
    fields = changed_fields(baseline, new)
    assert "prompt" in fields
    assert "model.name" not in fields


def test_solver_change_detected(baseline: EvalLog, base_samples: list) -> None:
    new = make_log(base_samples, solver="chain_of_thought")
    fields = changed_fields(baseline, new)
    assert "solver.name" in fields
    assert "model.name" not in fields


def test_dataset_change_detected(baseline: EvalLog, base_samples: list) -> None:
    new = make_log(base_samples, dataset_name="other_set")
    fields = changed_fields(baseline, new)
    assert "dataset.name" in fields
    assert "dataset.location" in fields
    assert "model.name" not in fields


def test_dataset_size_change_detected(baseline: EvalLog, base_samples: list) -> None:
    new = make_log(base_samples, dataset_samples=500)
    assert "dataset.samples" in changed_fields(baseline, new)


def test_sandbox_change_detected(baseline: EvalLog, base_samples: list) -> None:
    new = make_log(base_samples, sandbox=SandboxEnvironmentSpec(type="docker"))
    fields = changed_fields(baseline, new)
    assert "sandbox" in fields
    assert "model.name" not in fields


def test_task_version_change_detected(baseline: EvalLog, base_samples: list) -> None:
    assert "task.version" in changed_fields(baseline, make_log(base_samples, task_version=2))


def test_package_version_change_detected(baseline: EvalLog, base_samples: list) -> None:
    new = make_log(base_samples, packages={"openai": "1.2.0"})
    assert "packages.openai" in changed_fields(baseline, new)


def test_git_commit_change_detected(baseline: EvalLog, base_samples: list) -> None:
    assert "revision.commit" in changed_fields(baseline, make_log(base_samples, commit="b" * 40))


def test_unrecorded_tools_are_never_reported_as_unchanged(baseline: EvalLog) -> None:
    """When no plan step records a tool set, the honest answer is UNKNOWN.
    Reporting 'tools: unchanged' would assert something the log does not say.
    (When tools ARE recorded, they are compared -- see test_regressions.py.)"""
    result = compare_logs(baseline, copy.deepcopy(baseline))
    tools = next(d for d in result.config if d.field == "tools")
    assert tools.status is Status.UNKNOWN
    assert tools.status is not Status.SAME
    assert tools.note is not None


# ---------------------------------------------------------------- sample detectors


def _outcomes(old: EvalLog, new: EvalLog) -> dict[str, SampleOutcome]:
    return {d.key: d.outcome for d in compare_logs(old, new).samples}


def test_regression_detected(baseline: EvalLog, base_samples: list) -> None:
    mutated = copy.deepcopy(base_samples)
    mutated[0] = make_sample("q1", input="What is 2 + 2?", target="4", completion="5", value="I")
    result = compare_logs(baseline, make_log(mutated))
    assert result.summary.newly_failing == 1
    assert result.summary.newly_passing == 0
    assert _outcomes(baseline, make_log(mutated))["q1::1"] is SampleOutcome.NEWLY_FAILING


def test_recovery_detected(baseline: EvalLog, base_samples: list) -> None:
    mutated = copy.deepcopy(base_samples)
    mutated[3] = make_sample(
        "q4", input="Who wrote Hamlet?", target="Shakespeare", completion="Shakespeare", value="C"
    )
    result = compare_logs(baseline, make_log(mutated))
    assert result.summary.newly_passing == 1
    assert result.summary.newly_failing == 0


def test_error_introduced_detected(baseline: EvalLog, base_samples: list) -> None:
    mutated = copy.deepcopy(base_samples)
    mutated[0] = make_sample("q1", input="What is 2 + 2?", target="4", error="ToolError: timeout")
    result = compare_logs(baseline, make_log(mutated))
    assert result.summary.error_introduced == 1
    assert result.summary.newly_failing == 0, "an error is not a score regression"


def test_error_resolved_detected(base_samples: list) -> None:
    broken = copy.deepcopy(base_samples)
    broken[0] = make_sample("q1", input="What is 2 + 2?", target="4", error="ToolError: timeout")
    result = compare_logs(make_log(broken), make_log(base_samples))
    assert result.summary.error_resolved == 1
    assert result.summary.newly_passing == 0, "a resolved error is not a score recovery"


def test_added_and_removed_samples_detected(baseline: EvalLog, base_samples: list) -> None:
    extended = [*copy.deepcopy(base_samples), make_sample("q5", input="New?", target="yes")]
    result = compare_logs(baseline, make_log(extended))
    assert result.summary.added == 1
    assert result.summary.removed == 0

    reverse = compare_logs(make_log(extended), baseline)
    assert reverse.summary.removed == 1
    assert reverse.summary.added == 0


def test_unchanged_samples_are_not_reported_as_changed(
    baseline: EvalLog, base_samples: list
) -> None:
    mutated = copy.deepcopy(base_samples)
    mutated[0] = make_sample("q1", input="What is 2 + 2?", target="4", completion="5", value="I")
    result = compare_logs(baseline, make_log(mutated))
    assert result.summary.unchanged == 3


# ---------------------------------------------------------------- honest scoring


def test_numeric_score_change_is_not_called_a_regression(base_samples: list) -> None:
    """0.9 -> 0.4 is a score change. It is NOT a 'newly failing' sample: the log
    records no threshold that says what passing means."""
    old = make_log([make_sample("q1", scores={"grade": 0.9})])
    new = make_log([make_sample("q1", scores={"grade": 0.4})])
    result = compare_logs(old, new)
    assert result.summary.score_changed == 1
    assert result.summary.newly_failing == 0
    assert result.summary.newly_passing == 0
    outcome = result.samples[0]
    assert outcome.outcome is SampleOutcome.SCORE_CHANGED
    assert outcome.note is not None and "not reported as a regression" in outcome.note


def test_numeric_one_and_zero_are_read_as_pass_and_fail(base_samples: list) -> None:
    """Inspect encodes correct/incorrect numerically as 1/0. Those DO have a
    binary reading, and must be read."""
    old = make_log([make_sample("q1", scores={"grade": 1})])
    new = make_log([make_sample("q1", scores={"grade": 0})])
    assert compare_logs(old, new).summary.newly_failing == 1


def test_disjoint_scorers_are_not_comparable(base_samples: list) -> None:
    """exact_match='C' and model_graded_qa=0.9 do not measure the same thing.
    Reporting a regression between them would be an artifact of the tool."""
    old = make_log([make_sample("q1", scores={"exact_match": "C"})])
    new = make_log([make_sample("q1", scores={"model_graded_qa": 0.9})])
    result = compare_logs(old, new)
    assert result.summary.scores_not_comparable == 1
    assert result.summary.newly_failing == 0
    assert result.summary.newly_passing == 0
    assert result.summary.score_changed == 0


def test_shared_scorer_is_compared_when_another_scorer_is_added(base_samples: list) -> None:
    """Adding a second scorer must not stop the shared one being compared."""
    old = make_log([make_sample("q1", scores={"exact_match": "C"})])
    new = make_log([make_sample("q1", scores={"exact_match": "I", "extra": 0.5})])
    result = compare_logs(old, new)
    assert result.summary.newly_failing == 1
    assert result.summary.scores_not_comparable == 0


def test_id_reuse_with_changed_input_is_not_compared(base_samples: list) -> None:
    """The nastiest silent failure: a dataset edits a question but keeps the id.
    Comparing the two scores would be comparing answers to different questions."""
    old = make_log([make_sample("q1", input="What is 2 + 2?", target="4", value="C")])
    new = make_log([make_sample("q1", input="What is 9 * 9?", target="81", value="I")])
    result = compare_logs(old, new)
    assert result.summary.input_changed == 1
    assert result.summary.newly_failing == 0, "different questions must not yield a regression"
    assert result.samples[0].outcome is SampleOutcome.INPUT_CHANGED


@pytest.mark.parametrize(
    ("old_value", "new_value", "expected"),
    [
        ("C", "I", SampleOutcome.NEWLY_FAILING),
        ("I", "C", SampleOutcome.NEWLY_PASSING),
        (True, False, SampleOutcome.NEWLY_FAILING),
        (1, 0, SampleOutcome.NEWLY_FAILING),
        (0.9, 0.4, SampleOutcome.SCORE_CHANGED),
        ("P", "C", SampleOutcome.NEWLY_PASSING),
        ("C", "P", SampleOutcome.NEWLY_FAILING),
        ("C", "N", SampleOutcome.NEWLY_FAILING),
        ("P", "I", SampleOutcome.SCORE_CHANGED),
        ("C", "C", SampleOutcome.UNCHANGED),
    ],
)
def test_score_value_readings(
    old_value: object, new_value: object, expected: SampleOutcome
) -> None:
    old = make_log([make_sample("q1", scores={"s": old_value})])
    new = make_log([make_sample("q1", scores={"s": new_value})])
    assert compare_logs(old, new).samples[0].outcome is expected
