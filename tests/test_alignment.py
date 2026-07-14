"""Alignment tests.

Alignment is where a comparison tool can be confidently wrong. These tests pin
the guarantees:

* positional alignment is never used unless explicitly requested
* when it IS used, everything it produces is labelled weak
* a key that would align nothing is not used to declare everything added/removed
"""

from __future__ import annotations

import copy

from inspect_ai.log import EvalLog

from conftest import make_log, make_sample
from inspect_replay import align_samples, compare_logs
from inspect_replay.models import AlignmentMethod


def test_aligns_on_sample_id(baseline: EvalLog) -> None:
    alignment = align_samples(baseline, copy.deepcopy(baseline))
    assert alignment.method is AlignmentMethod.SAMPLE_ID
    assert len(alignment.pairs) == 4


def test_reordered_samples_still_align_by_id(baseline: EvalLog, base_samples: list) -> None:
    """Shuffling the sample list must not change the result. A positional tool
    would report every sample as changed here."""
    shuffled = make_log(list(reversed(copy.deepcopy(base_samples))))
    result = compare_logs(baseline, shuffled)
    assert result.alignment is AlignmentMethod.SAMPLE_ID
    assert result.summary.aligned == 4
    assert result.summary.unchanged == 4
    assert not result.samples_differ


def test_falls_back_to_input_hash_when_ids_are_unusable() -> None:
    """Ids that share no values cannot align anything, so the input hash is
    tried before giving up."""
    old = make_log([make_sample("old-1", input="Q one", target="a")])
    new = make_log([make_sample("new-1", input="Q one", target="a")])
    alignment = align_samples(old, new)
    assert alignment.method is AlignmentMethod.INPUT_HASH
    assert len(alignment.pairs) == 1


def test_positional_alignment_is_never_used_silently() -> None:
    """Nothing aligns: different ids AND different inputs. Without the explicit
    flag, the tool must refuse rather than guess by position."""
    old = make_log([make_sample("a", input="Q one", target="1")])
    new = make_log([make_sample("b", input="Q two", target="2")])

    alignment = align_samples(old, new)
    assert alignment.method is AlignmentMethod.NONE
    assert alignment.pairs == {}
    assert "NOT used" in alignment.note

    result = compare_logs(old, new)
    assert result.alignment is AlignmentMethod.NONE
    assert result.summary.aligned == 0
    assert result.summary.unalignable_old == 1
    assert result.summary.unalignable_new == 1
    # Unalignable samples must NOT be laundered into added/removed counts.
    assert result.summary.added == 0
    assert result.summary.removed == 0


def test_positional_alignment_when_requested_is_labelled_weak() -> None:
    old = make_log([make_sample("a", input="Q one", target="1")])
    new = make_log([make_sample("b", input="Q two", target="2")])

    result = compare_logs(old, new, allow_positional=True)
    assert result.alignment is AlignmentMethod.POSITIONAL
    assert result.summary.aligned == 1
    assert any("WEAK ALIGNMENT" in w for w in result.warnings)
    assert all(d.alignment_is_weak for d in result.samples)
    assert any("aligned by position" in o.statement for o in result.observations)


def test_no_samples_recorded_is_reported_not_treated_as_no_differences() -> None:
    """A log written with log_samples=False has no samples. That is 'we cannot
    tell', not 'nothing changed'."""
    old = make_log([])
    new = make_log([])
    result = compare_logs(old, new)
    assert result.alignment is AlignmentMethod.NONE
    assert any("no samples" in w for w in result.warnings)


def test_epochs_are_part_of_the_key() -> None:
    """The same sample id at epoch 1 and epoch 2 are distinct observations."""
    old = make_log([make_sample("q1", epoch=1, value="C"), make_sample("q1", epoch=2, value="C")])
    new = make_log([make_sample("q1", epoch=1, value="C"), make_sample("q1", epoch=2, value="I")])
    result = compare_logs(old, new)
    assert result.summary.aligned == 2
    assert result.summary.newly_failing == 1
    assert result.summary.unchanged == 1


def test_input_fingerprint_is_stable_and_ignores_output() -> None:
    """The alignment key must not depend on the things being compared."""
    from inspect_replay import input_fingerprint

    a = make_sample("q1", input="Same question", target="x", completion="answer one", value="C")
    b = make_sample("q1", input="Same question", target="x", completion="answer two", value="I")
    assert input_fingerprint(a) == input_fingerprint(b)

    c = make_sample("q1", input="Different question", target="x")
    assert input_fingerprint(a) != input_fingerprint(c)
