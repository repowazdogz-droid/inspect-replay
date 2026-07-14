"""CLI, determinism, read-only, JSON schema stability, and malformed input."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest
from inspect_ai.log import EvalLog

from conftest import EXAMPLES, make_log, make_sample
from inspect_replay import LoadError, compare, compare_logs, load_log, to_dict, to_json
from inspect_replay.cli import EXIT_DIFFERENCES, EXIT_ERROR, EXIT_NO_DIFFERENCES, main
from inspect_replay.models import SCHEMA_VERSION

BASELINE = EXAMPLES / "baseline.eval"
MODEL_CHANGE = EXAMPLES / "model-change.eval"
MALFORMED = EXAMPLES / "malformed.eval"


# ---------------------------------------------------------------- determinism


def test_output_is_deterministic() -> None:
    runs = [to_json(compare(BASELINE, MODEL_CHANGE)) for _ in range(5)]
    assert len(set(runs)) == 1, "the same inputs must produce byte-identical output"


def test_text_output_is_deterministic() -> None:
    from inspect_replay import render

    runs = [render(compare(BASELINE, MODEL_CHANGE), verbose=True) for _ in range(5)]
    assert len(set(runs)) == 1


def test_comparison_is_not_order_dependent_within_a_run(baseline: EvalLog) -> None:
    """Sample ordering in the output is sorted by key, not by list order."""
    keys = [d.key for d in compare_logs(baseline, copy.deepcopy(baseline)).samples]
    assert keys == sorted(keys)


# ---------------------------------------------------------------- read-only


def test_comparison_does_not_modify_the_input_logs() -> None:
    def fingerprint(p: Path) -> tuple[str, int, float]:
        data = p.read_bytes()
        st = p.stat()
        return hashlib.sha256(data).hexdigest(), st.st_size, st.st_mtime

    before = {p: fingerprint(p) for p in (BASELINE, MODEL_CHANGE)}
    compare(BASELINE, MODEL_CHANGE)
    after = {p: fingerprint(p) for p in (BASELINE, MODEL_CHANGE)}
    assert before == after, "inspect-replay must never write to the logs it reads"


def test_comparison_creates_no_files(tmp_path: Path) -> None:
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        compare(BASELINE, MODEL_CHANGE)
        assert list(tmp_path.iterdir()) == [], "comparison must not create side files"
    finally:
        os.chdir(cwd)


# ---------------------------------------------------------------- malformed input


def test_malformed_log_raises_a_clear_error() -> None:
    with pytest.raises(LoadError) as exc:
        load_log(MALFORMED)
    message = str(exc.value)
    assert "malformed.eval" in message
    assert "could not read Inspect log" in message


def test_missing_file_raises_a_clear_error() -> None:
    with pytest.raises(LoadError, match="does not exist"):
        load_log("/nonexistent/path/to.eval")


def test_directory_raises_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(LoadError, match="is a directory"):
        load_log(tmp_path)


def test_empty_file_raises_a_clear_error(tmp_path: Path) -> None:
    empty = tmp_path / "empty.eval"
    empty.write_bytes(b"")
    with pytest.raises(LoadError, match="is empty"):
        load_log(empty)


def test_malformed_log_exits_with_error_code(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["compare", str(BASELINE), str(MALFORMED)])
    assert code == EXIT_ERROR
    assert "error" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------- CLI contract


def test_cli_exit_code_zero_when_identical(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["compare", str(BASELINE), str(BASELINE)]) == EXIT_NO_DIFFERENCES
    assert "UNCHANGED" in capsys.readouterr().out


def test_cli_exit_code_one_when_different(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["compare", str(BASELINE), str(MODEL_CHANGE)]) == EXIT_DIFFERENCES
    assert "CHANGED" in capsys.readouterr().out


def test_cli_exit_zero_flag_suppresses_the_difference_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["compare", str(BASELINE), str(MODEL_CHANGE), "--exit-zero"])
    assert code == EXIT_NO_DIFFERENCES
    capsys.readouterr()


def test_cli_json_output_parses(capsys: pytest.CaptureFixture[str]) -> None:
    main(["compare", str(BASELINE), str(MODEL_CHANGE), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["verdict"] == "CHANGED"


def test_cli_writes_to_output_file(tmp_path: Path) -> None:
    out = tmp_path / "report.json"
    main(["compare", str(BASELINE), str(MODEL_CHANGE), "--json", "-o", str(out)])
    payload = json.loads(out.read_text())
    assert payload["tool"] == "inspect-replay"


def test_cli_rejects_positional_alignment_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    """The weak fallback must be opt-in from the command line too."""
    main(["compare", str(BASELINE), str(MODEL_CHANGE)])
    assert "POSITIONAL" not in capsys.readouterr().out


# ---------------------------------------------------------------- JSON schema


def test_json_schema_shape_is_stable() -> None:
    payload = to_dict(compare(BASELINE, MODEL_CHANGE))
    assert set(payload) == {
        "schema_version",
        "tool",
        "verdict",
        "sample_comparison_performed",
        "logs",
        "alignment",
        "results",
        "results_changed",
        "config",
        "config_changed",
        "summary",
        "samples",
        "observations",
        "warnings",
    }
    assert set(payload["summary"]) == {
        "unit",
        "old_total_samples",
        "new_total_samples",
        "old_distinct_samples",
        "new_distinct_samples",
        "aligned",
        "scored_denominator",
        "unchanged",
        "newly_passing",
        "newly_failing",
        "score_changed",
        "mixed",
        "error_introduced",
        "error_resolved",
        "errored_in_both",
        "added",
        "removed",
        "input_changed",
        "scores_not_comparable",
        "unknown",
        "unalignable_old",
        "unalignable_new",
    }
    assert set(payload["alignment"]) == {"method", "is_weak", "note"}
    sample = payload["samples"][0]
    assert {"key", "outcome", "alignment", "scores", "error", "tool_calls"} <= set(sample)


def test_json_denominators_are_always_present() -> None:
    """A regression count without a denominator is not actionable. Both totals
    and the aligned count are always emitted, even when zero."""
    payload = to_dict(compare(BASELINE, BASELINE))
    summary = payload["summary"]
    assert summary["old_total_samples"] == 8
    assert summary["new_total_samples"] == 8
    assert summary["aligned"] == 8
    assert summary["newly_failing"] == 0


def test_json_is_serialisable_and_sorted() -> None:
    text = to_json(compare(BASELINE, MODEL_CHANGE))
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True, ensure_ascii=True)


# ---------------------------------------------------------------- examples


def test_bundled_examples_load_and_compare() -> None:
    for name in ("baseline", "model-change", "scorer-change", "sample-regression"):
        assert (EXAMPLES / f"{name}.eval").exists(), f"missing example: {name}.eval"
        log = load_log(EXAMPLES / f"{name}.eval")
        assert log.samples and len(log.samples) == 8


def test_example_model_change_reports_model_and_flips() -> None:
    result = compare(BASELINE, MODEL_CHANGE)
    fields = {d.field for d in result.config_changes}
    assert "model.name" in fields
    assert "scorer" not in fields
    assert result.summary.newly_failing == 1
    assert result.summary.newly_passing == 1


def test_example_sample_regression_has_no_config_change_and_says_so() -> None:
    result = compare(BASELINE, EXAMPLES / "sample-regression.eval")
    assert result.config_changes == []
    assert result.summary.newly_failing == 2
    assert result.summary.error_introduced == 1
    assert any("not present in these logs" in o.statement for o in result.observations), (
        "when nothing in the config changed, the tool must say the source is unrecorded"
    )


def test_unrelated_sample_added_does_not_report_a_config_change() -> None:
    old = make_log([make_sample("q1")])
    new = make_log([make_sample("q1"), make_sample("q2", input="Another?", target="y")])
    result = compare_logs(old, new)
    assert result.summary.added == 1
    # dataset.samples and sample_ids legitimately change; the model must not.
    assert "model.name" not in {d.field for d in result.config_changes}


# ---------------------------------------------------------------- accounting


@pytest.mark.parametrize(
    "new_name",
    ["baseline.eval", "model-change.eval", "scorer-change.eval", "sample-regression.eval"],
)
def test_every_aligned_sample_lands_in_exactly_one_bucket(new_name: str) -> None:
    """A count that loses or double-counts samples is a count no one can act on."""
    s = compare(BASELINE, EXAMPLES / new_name).summary
    buckets = (
        s.unchanged
        + s.newly_passing
        + s.newly_failing
        + s.score_changed
        + s.mixed
        + s.error_introduced
        + s.error_resolved
        + s.errored_in_both
        + s.input_changed
        + s.scores_not_comparable
        + s.unknown
    )
    assert buckets == s.aligned, f"{buckets} bucketed vs {s.aligned} aligned"


def test_ci_gate_can_read_the_key_facts_from_json() -> None:
    """The shape a CI consumer actually depends on."""
    payload = to_dict(compare(BASELINE, EXAMPLES / "sample-regression.eval"))
    assert payload["sample_comparison_performed"] is True
    assert payload["summary"]["newly_failing"] == 2
    assert payload["summary"]["scored_denominator"] == 7
    assert payload["verdict"] == "CHANGED"
