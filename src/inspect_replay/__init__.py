"""inspect-replay: deterministic, sample-aligned comparison of Inspect AI eval logs.

Compares two recorded Inspect ``.eval`` logs and reports what changed in
configuration, samples, scores, and errors.

"Replay" here means reconstructing and comparing the RECORDED evaluation state.
It does not mean reproducing model-provider outputs: inspect-replay never calls
a model and never re-runs an evaluation. See ``docs/assurance-boundary.md``.

Public API::

    from inspect_replay import compare, to_dict, to_json, render

    result = compare("baseline.eval", "candidate.eval")
    print(result.verdict)          # Verdict.CHANGED
    print(render(result))          # human-readable report
    payload = to_dict(result)      # machine-readable, schema_version "0.1" (pre-1.0)
"""

from __future__ import annotations

__version__ = "0.2.0"

from .align import Alignment, align_samples, input_fingerprint
from .compare import compare, compare_logs
from .config_diff import compare_config, compare_results
from .json_output import to_dict, to_json
from .loader import LoadError, load_log
from .models import (
    SCHEMA_VERSION,
    AlignmentMethod,
    Comparison,
    FieldDiff,
    Observation,
    PassFail,
    SampleDiff,
    SampleOutcome,
    SampleSummary,
    ScoreDiff,
    Status,
    Verdict,
)
from .report import render
from .sample_diff import compare_samples, score_numeric, score_pass_fail

__all__ = [
    "SCHEMA_VERSION",
    "Alignment",
    "AlignmentMethod",
    "Comparison",
    "FieldDiff",
    "LoadError",
    "Observation",
    "PassFail",
    "SampleDiff",
    "SampleOutcome",
    "SampleSummary",
    "ScoreDiff",
    "Status",
    "Verdict",
    "__version__",
    "align_samples",
    "compare",
    "compare_config",
    "compare_logs",
    "compare_results",
    "compare_samples",
    "input_fingerprint",
    "load_log",
    "render",
    "score_numeric",
    "score_pass_fail",
    "to_dict",
    "to_json",
]
