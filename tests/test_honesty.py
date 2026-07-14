"""Tests of the claims the tool makes about itself.

These are the tests that stop the tool from drifting into overclaiming as it is
maintained. If someone later writes "this regression was caused by the
temperature change" into a report string, the build fails here.
"""

from __future__ import annotations

import copy
import re

from inspect_ai.log import EvalLog
from inspect_ai.util import SandboxEnvironmentSpec

from conftest import EXAMPLES, make_log, make_sample
from inspect_replay import compare, compare_logs, render, to_dict, to_json
from inspect_replay.models import Status

# Vocabulary that asserts a causal link. inspect-replay reads two recorded
# artifacts; it cannot hold a variable fixed and cannot re-run anything, so it
# is not entitled to any of these words.
CAUSAL_PATTERNS = [
    r"\bcaused by\b",
    r"\bcauses\b",
    r"\bbecause of\b",
    r"\bdue to\b",
    r"\bresulted in\b",
    r"\bresponsible for\b",
    r"\bexplains the\b",
    r"\bthis is why\b",
    r"\bproves\b",
]

ALL_EXAMPLE_PAIRS = [
    ("baseline.eval", "model-change.eval"),
    ("baseline.eval", "scorer-change.eval"),
    ("baseline.eval", "sample-regression.eval"),
    ("baseline.eval", "baseline.eval"),
]


def _all_report_text(old: EvalLog, new: EvalLog) -> str:
    result = compare_logs(old, new)
    return render(result, verbose=True) + "\n" + to_json(result)


def test_no_causal_wording_in_any_generated_report(baseline: EvalLog, base_samples: list) -> None:
    """Across every scenario the tool can report on, no output asserts causation."""
    scenarios: list[EvalLog] = [
        copy.deepcopy(baseline),
        make_log(base_samples, model="openai/gpt-4o"),
        make_log(base_samples, temperature=0.7),
        make_log(base_samples, scorer="model_graded_qa"),
        make_log(base_samples, sandbox=SandboxEnvironmentSpec(type="docker")),
        make_log(base_samples, dataset_name="other"),
        make_log([make_sample("q1", error="ToolError: boom")]),
        make_log([]),
    ]
    for new in scenarios:
        text = _all_report_text(baseline, new).lower()
        for pattern in CAUSAL_PATTERNS:
            assert not re.search(pattern, text), (
                f"causal wording {pattern!r} in report for {new.eval.model}"
            )


def test_no_causal_wording_in_bundled_example_reports() -> None:
    for old_name, new_name in ALL_EXAMPLE_PAIRS:
        result = compare(EXAMPLES / old_name, EXAMPLES / new_name)
        text = (render(result, verbose=True) + to_json(result)).lower()
        for pattern in CAUSAL_PATTERNS:
            assert not re.search(pattern, text), (
                f"causal wording {pattern!r} in {old_name}->{new_name}"
            )


def test_observations_are_labelled_as_co_occurrence() -> None:
    result = compare(EXAMPLES / "baseline.eval", EXAMPLES / "model-change.eval")
    payload = to_dict(result)
    assert payload["observations"], "expected at least one observation"
    for obs in payload["observations"]:
        assert obs["relationship"] == "co-occurrence"
        assert obs["evidence"], "every observation must name the fields it was derived from"


def test_every_config_conclusion_carries_evidence(baseline: EvalLog) -> None:
    result = compare_logs(baseline, copy.deepcopy(baseline))
    for d in result.config:
        assert d.evidence, f"field {d.field} reports a conclusion with no evidence"


def test_unknown_is_never_encoded_as_false_in_json(baseline: EvalLog) -> None:
    """A consumer must be able to distinguish 'unchanged' from 'not recorded'.
    Encoding the latter as false or as SAME would silently mislead."""
    payload = to_dict(compare_logs(baseline, copy.deepcopy(baseline)))
    statuses = {d["field"]: d["status"] for d in payload["config"]}
    assert statuses["sandbox"] == "UNKNOWN"
    assert statuses["tools"] == "UNKNOWN"
    assert statuses["model.name"] == "SAME"
    assert statuses["task.args"] == "SAME", "a recorded empty {} is SAME, not UNKNOWN"
    for d in payload["config"]:
        assert d["status"] in {s.value for s in Status}
        assert not isinstance(d["status"], bool)


def test_verdict_unchanged_only_when_nothing_observed(baseline: EvalLog) -> None:
    """UNCHANGED must never be reported when something did in fact differ."""
    same = compare_logs(baseline, copy.deepcopy(baseline))
    assert same.verdict.value == "UNCHANGED"

    for new in (
        make_log(list(baseline.samples or []), model="other/model"),
        make_log(list(baseline.samples or []), temperature=1.0),
    ):
        assert compare_logs(baseline, new).verdict.value == "CHANGED"


def test_report_states_its_own_boundary(baseline: EvalLog) -> None:
    text = render(compare_logs(baseline, copy.deepcopy(baseline)))
    assert "does not re-run the evaluation" in text
    assert "assurance-boundary" in text
