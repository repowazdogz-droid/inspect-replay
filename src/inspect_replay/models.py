"""Core data model for a comparison between two Inspect evaluation logs.

The model is deliberately explicit about ignorance. A field that was not
recorded in the log is ``NOT_CHECKED`` or ``UNKNOWN`` -- never ``SAME`` and
never ``False``. A comparison that could not be performed is ``NOT_COMPARABLE``
-- never ``UNCHANGED``. See ``docs/assurance-boundary.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

SCHEMA_VERSION = "0.1"
"""Version of the JSON output schema.

Deliberately ``0.x``: while the package is pre-1.0 the schema is not frozen and
may change between minor versions. Consumers should pin a version and read the
contract/presentation split documented in ``json_output.py`` -- in short, the
machine contract is the enum-valued and numeric fields; ``observations[].
statement`` and other prose fields are human-facing and may be reworded without
a version bump. The version moves to ``1.0`` when the schema is frozen."""


class Status(StrEnum):
    """Outcome of comparing a single field across the two logs."""

    SAME = "SAME"
    CHANGED = "CHANGED"
    ADDED = "ADDED"
    """Present in the new log, absent in the old."""
    REMOVED = "REMOVED"
    """Present in the old log, absent in the new."""
    UNKNOWN = "UNKNOWN"
    """The field exists in the schema but its value was not recorded in at
    least one log, so no comparison is possible."""
    NOT_CHECKED = "NOT_CHECKED"
    """inspect-replay does not compare this field."""


class Verdict(StrEnum):
    """Top-level result of the comparison."""

    UNCHANGED = "UNCHANGED"
    """No recorded configuration difference and no sample outcome difference.
    Only reported when the sample comparison was actually performed."""
    CHANGED = "CHANGED"
    """At least one recorded difference was observed."""
    NOT_COMPARABLE = "NOT_COMPARABLE"
    """The comparison could not be performed: the logs record no samples, or no
    sample could be aligned. This is NOT 'unchanged'. A tool that returns
    'unchanged' when it compared nothing is a tool that passes a CI gate on an
    evaluation it never looked at.
    """


class PassFail(StrEnum):
    """Interpretation of a score value, where one is defined.

    Inspect score values may be str ("C"/"I"/"P"/"N"), bool, numeric, or a
    list/dict. Only some of those carry a graded reading. A numeric score of 0.7
    is NOT a pass; it is ``UNKNOWN`` here and is compared as a magnitude.
    """

    PASS = "PASS"
    FAIL = "FAIL"
    PARTIAL = "PARTIAL"
    NO_ANSWER = "NO_ANSWER"
    UNKNOWN = "UNKNOWN"
    """No graded reading is defined for this value type."""

    @property
    def is_graded(self) -> bool:
        """True where Inspect defines a correctness reading for this value.

        PARTIAL and NO_ANSWER are graded: Inspect's own ``value_to_float`` maps
        them to 0.5 and 0.0. Treating them as unreadable would drop a real
        degradation (a model going from correct to refusing to answer) out of
        the regression count.
        """
        return self is not PassFail.UNKNOWN


class AlignmentMethod(StrEnum):
    """How the two sample sets were put into correspondence.

    Ordered strongest first. ``POSITIONAL`` is a weak fallback and is never
    used unless the caller explicitly opts in.
    """

    SAMPLE_ID = "SAMPLE_ID"
    """Aligned on the recorded (sample id, epoch). Strongest available key."""
    INPUT_HASH = "INPUT_HASH"
    """Aligned on a content hash of the sample input and target, used when ids
    are missing, duplicated, or share no values across the two logs."""
    POSITIONAL = "POSITIONAL"
    """WEAK. Aligned on position in the sample list. Only used when explicitly
    requested. Any comparison built on it is labelled unreliable."""
    NONE = "NONE"
    """No alignment was possible."""


class SampleOutcome(StrEnum):
    """Classification of one aligned or unaligned sample."""

    UNCHANGED = "UNCHANGED"
    NEWLY_PASSING = "NEWLY_PASSING"
    """Was not scored correct, now is."""
    NEWLY_FAILING = "NEWLY_FAILING"
    """Was scored correct, now is not. Includes a drop to PARTIAL or NO_ANSWER:
    the model no longer answers the question correctly, and Inspect's own
    accuracy metric falls accordingly."""
    SCORE_CHANGED = "SCORE_CHANGED"
    """The score changed, but not across the correct/not-correct boundary, or
    the value type carries no graded reading (e.g. a bare number). This cannot
    be called a regression or a recovery."""
    MIXED = "MIXED"
    """Multiple scorers moved in opposite directions on this sample: at least
    one newly passing and at least one newly failing. Calling this sample a
    regression or a recovery would discard half the evidence."""
    ERROR_INTRODUCED = "ERROR_INTRODUCED"
    ERROR_RESOLVED = "ERROR_RESOLVED"
    ERRORED_IN_BOTH = "ERRORED_IN_BOTH"
    """Errored in both runs. No score exists on either side, so this sample
    contributes nothing to a pass rate and must not be counted as unchanged."""
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    INPUT_CHANGED = "INPUT_CHANGED"
    """The sample id matched but the recorded input differs. The two samples
    are not the same question, so their scores are NOT compared."""
    SCORES_NOT_COMPARABLE = "SCORES_NOT_COMPARABLE"
    """The two runs share no scorer, so there is no common quantity to compare.
    An exact_match value of "C" and a model_graded_qa value of 0.9 do not
    measure the same thing."""
    UNKNOWN = "UNKNOWN"
    """Aligned, but no score and no error on at least one side."""


@dataclass(frozen=True)
class FieldDiff:
    """One compared configuration field.

    ``evidence`` names the log fields the conclusion was read from, using
    dotted paths into the Inspect ``EvalLog`` schema (e.g.
    ``eval.model_generate_config.temperature``). Every conclusion this tool
    reports must be traceable to the fields it was derived from.

    Note: instances are frozen but not reliably hashable, because ``old`` and
    ``new`` hold arbitrary recorded values including lists and dicts.
    """

    field: str
    status: Status
    old: Any = None
    new: Any = None
    evidence: tuple[str, ...] = ()
    note: str | None = None
    """Why a field is UNKNOWN or NOT_CHECKED, when that needs saying."""

    @property
    def differs(self) -> bool:
        return self.status in (Status.CHANGED, Status.ADDED, Status.REMOVED)


@dataclass(frozen=True)
class ScoreDiff:
    """Change in one named scorer's value for one sample.

    ``old_present``/``new_present`` distinguish "this scorer recorded the value
    None" from "this scorer did not run in that log". Only a scorer present on
    both sides measures a comparable quantity.
    """

    scorer: str
    old_value: Any
    new_value: Any
    old_pass_fail: PassFail
    new_pass_fail: PassFail
    old_numeric: float | None = None
    new_numeric: float | None = None
    old_present: bool = True
    new_present: bool = True

    @property
    def comparable(self) -> bool:
        return self.old_present and self.new_present

    @property
    def changed(self) -> bool:
        """True only where both sides ran this scorer and the values differ."""
        return self.comparable and self.old_value != self.new_value

    @property
    def graded(self) -> bool:
        """True where both sides carry a correctness reading."""
        return self.comparable and self.old_pass_fail.is_graded and self.new_pass_fail.is_graded

    @property
    def equivalent(self) -> bool:
        """True where the two values encode the same graded outcome.

        A scorer that switches encoding from ``"C"`` to ``1.0`` has not changed
        its verdict, and reporting a change would be an artifact of the
        encoding rather than a fact about the evaluation.
        """
        return self.graded and self.old_pass_fail is self.new_pass_fail


@dataclass(frozen=True)
class SampleDiff:
    """One sample's fate across the two runs."""

    key: str
    """The alignment key, rendered as a string for stable output."""
    sample_id: str | int | None
    epoch: int | None
    outcome: SampleOutcome
    alignment: AlignmentMethod
    scores: tuple[ScoreDiff, ...] = ()
    old_error: str | None = None
    new_error: str | None = None
    input_changed: bool = False
    completion_changed: Status = Status.NOT_CHECKED
    tool_calls_old: int | None = None
    tool_calls_new: int | None = None
    metadata_changed: Status = Status.NOT_CHECKED
    reduced: bool = False
    """This row is a per-sample reduced score (from EvalLog.reductions), not a
    single epoch's row."""
    note: str | None = None

    @property
    def alignment_is_weak(self) -> bool:
        return self.alignment is AlignmentMethod.POSITIONAL


@dataclass(frozen=True)
class SampleSummary:
    """Counts over the sample comparison.

    Denominators are explicit on purpose: a count of regressions is meaningless
    without the number of rows it was drawn from and the number that could not
    be compared at all.

    ``unit`` names what is being counted. With ``epochs > 1`` a single dataset
    sample produces several rows, and calling those rows "samples" would report
    a denominator that does not exist in the dataset.
    """

    old_total: int
    new_total: int
    aligned: int
    unit: str = "sample"
    """Either "sample" or "sample-epoch row"."""
    old_distinct_samples: int = 0
    new_distinct_samples: int = 0
    unchanged: int = 0
    newly_passing: int = 0
    newly_failing: int = 0
    score_changed: int = 0
    mixed: int = 0
    error_introduced: int = 0
    error_resolved: int = 0
    errored_in_both: int = 0
    added: int = 0
    removed: int = 0
    input_changed: int = 0
    scores_not_comparable: int = 0
    unknown: int = 0
    unalignable_old: int = 0
    """Samples in the old log that could not be matched to any new sample."""
    unalignable_new: int = 0
    """Samples in the new log that could not be matched to any old sample."""

    @property
    def scored_denominator(self) -> int:
        """Aligned rows that carry a comparable score on BOTH sides.

        This is the only denominator a pass rate may legitimately be computed
        over. It excludes every row that does not have a score on both sides to
        compare: rows that errored in either run or both, rows whose input
        changed, rows whose scorers do not correspond, and rows whose outcome is
        unknown. An errored sample has no score, so counting it in the
        denominator silently depresses any rate computed from this report.
        """
        return self.aligned - (
            self.errored_in_both
            + self.error_introduced
            + self.error_resolved
            + self.input_changed
            + self.scores_not_comparable
            + self.unknown
        )

    @property
    def differs(self) -> bool:
        """Whether any sample-level difference was observed."""
        return bool(
            self.newly_passing
            or self.newly_failing
            or self.score_changed
            or self.mixed
            or self.error_introduced
            or self.error_resolved
            or self.added
            or self.removed
            or self.input_changed
            or self.scores_not_comparable
        )


@dataclass(frozen=True)
class Observation:
    """A ranked, evidence-bearing statement about what changed alongside what.

    This is NOT a causal claim. ``inspect-replay`` observes co-occurrence in
    two recorded artifacts; it cannot run the evaluation, cannot hold a
    variable fixed, and therefore cannot establish causation. Wording is
    constrained accordingly -- see ``docs/assurance-boundary.md``.
    """

    statement: str
    evidence: tuple[str, ...]
    rank: int


@dataclass(frozen=True)
class Comparison:
    """The complete result of comparing two logs."""

    verdict: Verdict
    old_location: str
    new_location: str
    config: tuple[FieldDiff, ...]
    results: tuple[FieldDiff, ...]
    """Headline metrics from EvalResults (e.g. accuracy). These are recorded
    even when samples are not, and are the numbers the evaluation reports."""
    samples: tuple[SampleDiff, ...]
    summary: SampleSummary
    observations: tuple[Observation, ...]
    alignment: AlignmentMethod
    alignment_note: str
    sample_comparison_performed: bool
    warnings: tuple[str, ...] = ()
    old_status: str = "unknown"
    new_status: str = "unknown"

    @property
    def config_changes(self) -> list[FieldDiff]:
        return [d for d in self.config if d.differs]

    @property
    def result_changes(self) -> list[FieldDiff]:
        return [d for d in self.results if d.differs]

    @property
    def samples_differ(self) -> bool:
        return self.summary.differs


__all__ = [
    "SCHEMA_VERSION",
    "AlignmentMethod",
    "Comparison",
    "FieldDiff",
    "Observation",
    "PassFail",
    "SampleDiff",
    "SampleOutcome",
    "SampleSummary",
    "ScoreDiff",
    "Status",
    "Verdict",
]
