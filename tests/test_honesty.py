"""Tests of the claims the tool makes about itself.

These are the tests that stop the tool from drifting into overclaiming as it is
maintained. The load-bearing one is the causal-language check: the tool's
authored prose must never assert that one change brought about an outcome, only
that things changed together.

The guarantee is scoped precisely, and the scope is the honest part. It covers
the text inspect-replay AUTHORS -- observation statements, field and sample
notes, warnings, the alignment note. It does NOT cover verbatim content quoted
from the log (an error message, a score value): a crafted log can contain the
word "caused" and the tool reproduces it as data. That gap is why the tool no
longer splices raw error text into its authored sentences, and why the
assurance boundary states the limit plainly.
"""

from __future__ import annotations

import copy

from inspect_ai.log import EvalLog
from inspect_ai.util import SandboxEnvironmentSpec

from _causal import causal_language
from conftest import EXAMPLES, make_log, make_sample
from inspect_replay import compare, compare_logs, render, to_dict, to_json
from inspect_replay.models import Comparison, Status

ALL_EXAMPLE_PAIRS = [
    ("baseline.eval", "model-change.eval"),
    ("baseline.eval", "scorer-change.eval"),
    ("baseline.eval", "sample-regression.eval"),
    ("baseline.eval", "baseline.eval"),
]

# The four causal sentences that the old substring blocklist waved through.
# They are the acceptance test for the detector: if any of these ever passes
# again, the guarantee is hollow.
CAUSAL_SENTENCES_THAT_MUST_BE_CAUGHT = [
    "the temperature change caused the regression",
    "the model change led to failures",
    "the new prompt triggered errors",
    "lowering max_tokens drove accuracy down",
]


def _authored_prose(comparison: Comparison) -> str:
    """Every string inspect-replay composes for one comparison.

    Deliberately excludes verbatim log content: score values, error messages,
    completions, and locations are data the tool reproduces, not claims it
    makes. The causal-language guarantee is over claims, not over quotes.
    """
    parts: list[str] = [o.statement for o in comparison.observations]
    parts += [d.note for d in comparison.config if d.note]
    parts += [d.note for d in comparison.results if d.note]
    parts += [d.note for d in comparison.samples if d.note]
    parts += list(comparison.warnings)
    parts.append(comparison.alignment_note)
    return "\n".join(p for p in parts if p)


def test_detector_catches_the_four_sentences_the_old_blocklist_missed() -> None:
    """Regression test for the hollow guarantee. Each of these passed the old
    substring blocklist; each must now be caught."""
    for sentence in CAUSAL_SENTENCES_THAT_MUST_BE_CAUGHT:
        assert causal_language(sentence) is not None, (
            f"causal detector failed to catch: {sentence!r}"
        )


def test_detector_does_not_flag_the_tools_own_hedged_prose() -> None:
    """A detector that flags 'possible contributor' or 'changed alongside' would
    be useless -- it would force the honest wording out."""
    for benign in [
        "changed alongside the outcomes below",
        "a possible contributor to every score difference",
        "cannot be determined from the recorded data",
        "A scorer change alters how outcomes are computed from the same model output",
        "no recorded configuration field that bears on them changed",
        "an error suppresses a score, so their contribution changed",
    ]:
        assert causal_language(benign) is None, f"false positive on: {benign!r}"


def test_no_causal_wording_in_authored_prose_across_scenarios(
    baseline: EvalLog, base_samples: list
) -> None:
    """Across scenarios, the tool's authored prose asserts no causation.

    Every scenario must exercise a DIFFERENT observation/note branch, because the
    detector can only be tripped by a branch that actually runs -- an earlier
    version's INPUT_CHANGED observation said "...because they are no longer the
    same question", which trips the detector, and no scenario ran it. Each
    labelled pair below is chosen to fire a specific branch.
    """
    scenarios: list[tuple[str, EvalLog, EvalLog, bool]] = [
        ("identical", baseline, copy.deepcopy(baseline), False),
        ("model", baseline, make_log(base_samples, model="openai/gpt-4o"), False),
        ("temperature", baseline, make_log(base_samples, temperature=0.7), False),
        ("scorer", baseline, make_log(base_samples, scorer="model_graded_qa"), False),
        (
            "sandbox",
            baseline,
            make_log(base_samples, sandbox=SandboxEnvironmentSpec(type="docker")),
            False,
        ),
        ("dataset", baseline, make_log(base_samples, dataset_name="other"), False),
        (
            "error_introduced",
            baseline,
            make_log([make_sample("q1", error="ToolError: boom")]),
            False,
        ),
        ("no_samples", baseline, make_log([]), False),
        # branches the original set never exercised:
        (
            "input_changed",
            make_log([make_sample("q1", input="a", value="C")]),
            make_log([make_sample("q1", input="b", value="I")]),
            False,
        ),
        (
            "errored_in_both",
            make_log([make_sample("q1", error="e1")]),
            make_log([make_sample("q1", error="e2")]),
            False,
        ),
        (
            "mixed",
            make_log([make_sample("q1", scores={"a": "C", "b": "I"})]),
            make_log([make_sample("q1", scores={"a": "I", "b": "C"})]),
            False,
        ),
        (
            "scores_not_comparable",
            make_log([make_sample("q1", scores={"x": "C"})]),
            make_log([make_sample("q1", scores={"y": "I"})]),
            False,
        ),
        (
            "positional",
            make_log([make_sample("a", input="p", value="C")]),
            make_log([make_sample("z", input="q", value="I")]),
            True,
        ),
    ]
    for label, old, new, positional in scenarios:
        result = compare_logs(old, new, allow_positional=positional)
        hit = causal_language(_authored_prose(result))
        assert hit is None, f"authored prose asserts causation ({hit!r}) in the {label!r} scenario"


def test_full_rendered_report_has_no_causal_wording_on_benign_logs() -> None:
    """End-to-end over the bundled examples: the whole rendered report, including
    the fixed scaffolding and disclaimer, is free of causal language. (Benign
    logs carry no causal content of their own, so this also exercises the static
    strings.)"""
    for old_name, new_name in ALL_EXAMPLE_PAIRS:
        result = compare(EXAMPLES / old_name, EXAMPLES / new_name)
        text = render(result, verbose=True) + "\n" + to_json(result)
        hit = causal_language(text)
        assert hit is None, f"causal wording {hit!r} in {old_name}->{new_name}"


def test_a_causal_error_string_in_the_log_never_enters_authored_prose() -> None:
    """A crafted log whose error message asserts causation must not put that
    assertion into a sentence the TOOL authors. The message is reproduced as a
    quoted value; the tool's own claims stay causation-free."""
    old = [make_sample("q1")]
    new = [
        make_sample(
            "q1",
            error="ToolError: the timeout caused the failure and led to a crash",
        )
    ]
    result = compare_logs(make_log(old), make_log(new))

    prose = _authored_prose(result)
    hit = causal_language(prose)
    assert hit is None, f"an injected causal error reached authored prose: {hit!r}"

    # And it is still surfaced to the user as a quoted value, not hidden.
    assert "caused the failure" in render(result, verbose=True)


def test_a_causal_model_name_never_enters_authored_prose() -> None:
    """A model NAME is untrusted log content. Naming a model
    'gpt4-which-caused-the-regression' must not make the tool author a sentence
    that reads as a causal claim -- the value is shown under Configuration, not
    spliced into the observation."""
    old = make_log([make_sample("q1")], model="ok/model")
    new = make_log([make_sample("q1", value="I")], model="gpt4-which-caused-the-regression")
    result = compare_logs(old, new)

    hit = causal_language(_authored_prose(result))
    assert hit is None, f"a causal model name reached authored prose: {hit!r}"
    # still visible to the reader, under Configuration.
    assert "caused-the-regression" in render(result)


def test_untrusted_metric_and_generation_values_are_not_spliced_into_prose() -> None:
    """Metric values and generation-config values are untrusted and are shown
    under their sections, not spliced into observation statements."""
    old = make_log([make_sample("q1")], accuracy=0.9, temperature=0.0)
    new = make_log([make_sample("q1", value="I")], accuracy=0.5, temperature=0.7)
    prose = _authored_prose(compare_logs(old, new))
    # the observation names the field/section, not the raw values.
    assert "0.9" not in prose and "0.5" not in prose


def test_a_causal_scorer_name_never_enters_a_sample_note() -> None:
    """A scorer NAME is untrusted. When the two runs share no scorer, the
    per-sample note must not splice the names -- a scorer named to contain
    'caused' would otherwise put causation into the tool's authored note."""
    old = make_log([make_sample("q1", scores={"exact_match": "C"})])
    new = make_log([make_sample("q1", scores={"grader-that-caused-the-regression": "I"})])
    result = compare_logs(old, new)

    hit = causal_language(_authored_prose(result))
    assert hit is None, f"a causal scorer name reached an authored note: {hit!r}"
    # still visible to the reader on the sample line.
    assert "caused-the-regression" in render(result, verbose=True)


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


# --------------------------------------------------------------------------
# Terminal-injection: an .eval is untrusted. Its error strings, score values,
# and locations must not carry control sequences into a terminal. A crafted log
# could otherwise clear the screen and print a forged green verdict.
# --------------------------------------------------------------------------

_ANSI_ATTACK = "\x1b[2J\x1b[H\x1b[1;32mEvaluation: UNCHANGED\x1b[0m\x07"


def test_ansi_escapes_in_an_error_never_reach_the_text_report() -> None:
    old = [make_sample("q1")]
    new = [make_sample("q1", error=f"ToolError: {_ANSI_ATTACK}")]
    text = render(compare_logs(make_log(old), make_log(new)), verbose=True)

    assert "\x1b" not in text, "raw ESC reached the text report"
    assert "\x07" not in text, "raw BEL reached the text report"
    for ch in text:
        assert ord(ch) >= 0x20 or ch == "\n", f"control char {ord(ch):#x} in report"


def test_ansi_escapes_in_a_score_value_never_reach_the_text_report() -> None:
    old = [make_sample("q1", scores={"s": "C"})]
    new = [make_sample("q1", scores={"s": _ANSI_ATTACK})]
    text = render(compare_logs(make_log(old), make_log(new)), verbose=True)
    assert "\x1b" not in text


def test_ansi_escapes_in_the_location_never_reach_the_text_report() -> None:
    old = make_log([make_sample("q1")], location=f"old{_ANSI_ATTACK}.eval")
    new = make_log([make_sample("q1")], location="new.eval")
    text = render(compare_logs(old, new))
    assert "\x1b" not in text


def _has_control(text: str) -> bool:
    return any(
        (ord(c) < 0x20 and c != "\n") or ord(c) == 0x7F or 0x80 <= ord(c) <= 0x9F for c in text
    )


def test_ansi_escapes_in_identifier_fields_never_reach_the_text_report() -> None:
    """The escapes hide in identifiers as readily as in values: a model name, a
    package field name, a sample id, a scorer name are all untrusted."""
    vectors = {
        "model": compare_logs(
            make_log([make_sample("q1")], model="a"),
            make_log([make_sample("q1", value="I")], model=f"evil/{_ANSI_ATTACK}"),
        ),
        "package_field": compare_logs(
            make_log([make_sample("q1")], packages={f"p{_ANSI_ATTACK}k": "1"}),
            make_log([make_sample("q1")], packages={f"p{_ANSI_ATTACK}k": "2"}),
        ),
        "sample_id": compare_logs(
            make_log([make_sample(f"id{_ANSI_ATTACK}", value="C")]),
            make_log([make_sample(f"id{_ANSI_ATTACK}", value="I")]),
        ),
        "scorer": compare_logs(
            make_log([make_sample("q1", scores={f"s{_ANSI_ATTACK}": "C"})]),
            make_log([make_sample("q1", scores={f"s{_ANSI_ATTACK}": "I"})]),
        ),
    }
    for name, result in vectors.items():
        text = render(result, verbose=True)
        assert not _has_control(text), f"control char reached the report via {name}"


def test_c1_and_other_control_bytes_are_neutralised() -> None:
    """Not only ESC: the C1 CSI (0x9b), backspace, and carriage return are also
    terminal-active and must be stripped."""
    for attack in ("\x9bbad", "\x08\x08overwrite", "\rforge"):
        new = [make_sample("q1", error=f"ToolError: {attack}")]
        text = render(compare_logs(make_log([make_sample("q1")]), make_log(new)), verbose=True)
        assert not _has_control(text), f"control char survived: {attack!r}"


def test_sanitising_preserves_legitimate_unicode() -> None:
    """The fix must neutralise control characters without corrupting real text:
    accented letters, CJK, the arrow and ellipsis the report itself uses."""
    new = [make_sample("q1", error="café → ✓ … résumé 日本語")]
    text = render(compare_logs(make_log([make_sample("q1")]), make_log(new)), verbose=True)
    for token in ("café", "→", "✓", "…", "日本語"):
        assert token in text, f"legitimate unicode {token!r} was corrupted"


def test_json_output_escapes_every_control_byte_not_just_esc() -> None:
    """`json.dumps(ensure_ascii=False)` escapes only C0 (through ESC) and passes
    DEL (0x7f) and the C1 range (0x80-0x9f, including the C1 CSI 0x9b) through
    raw. The tool uses ensure_ascii=True to close that. Feed all three and
    assert none survives raw."""
    for attack in ("\x1b[31m", "\x7fDEL", "\x9bCSI", "\x80\x9f"):
        old = [make_sample("q1")]
        new = [make_sample("q1", error=f"ToolError: {attack}")]
        payload = to_json(compare_logs(make_log(old), make_log(new)))
        assert not _has_control(payload), f"raw control byte in JSON for {attack!r}"
    # ESC is still present, escaped (stripping would lose information silently).
    esc_payload = to_json(
        compare_logs(make_log([make_sample("q1")]), make_log([make_sample("q1", error="\x1b")]))
    )
    assert "\\u001b" in esc_payload, "ESC should appear escaped, not stripped"
