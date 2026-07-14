# inspect-replay

Deterministic, sample-aligned comparison of two [Inspect AI](https://inspect.aisi.org.uk)
evaluation logs.

[![CI](https://github.com/repowazdogz-droid/inspect-replay/actions/workflows/ci.yml/badge.svg)](https://github.com/repowazdogz-droid/inspect-replay/actions/workflows/ci.yml)
[![Python 3.11–3.13](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org/)
[![Checked: mypy --strict, ruff](https://img.shields.io/badge/checked-mypy%20--strict%20%C2%B7%20ruff-brightgreen)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

An Inspect evaluation result moved between two runs. You have the two `.eval`
logs and one question: what changed? `inspect-replay` answers it by comparing the
recorded state of the two runs. It reports which configuration fields differ,
which headline metrics moved, which samples regressed, recovered, or errored, and
which samples could not be compared at all, and it is deliberate about the
difference between "unchanged" and "we cannot tell".

```console
$ inspect-replay compare examples/baseline.eval examples/sample-regression.eval
```

```
Evaluation: CHANGED
  old: examples/baseline.eval
  new: examples/sample-regression.eval

Reported metrics:
- results.exact_match.accuracy: 0.75 → 0.5
- results.completed_samples: 8 → 7

Configuration:
- no recorded configuration field changed
- 24 other field(s): unchanged
- 5 field(s): UNKNOWN, not recorded in at least one log (not the same as unchanged): model.base_url, model.roles, sandbox, tools, revision.dirty

Samples (unit: sample):
- 8 in old log, 8 in new log
- 8 aligned (aligned on recorded sample id and epoch)
- 4 unchanged
- 2 newly failing
- 1 newly passing
- 1 errors introduced
- 7 carry a comparable score on both sides (the only denominator a pass rate may be computed over)

Changed samples:
  q1::1: newly failing
      exact_match: C → I
  q2::1: newly failing
      exact_match: C → I
  q4::1: error introduced
      error: ToolError: bash sandbox exited with code 137 (out of memory)
  q7::1: newly passing
      exact_match: I → C

What changed alongside the result (ranked; co-occurrence, not causation):
1. 1 of the evaluation's own reported metric(s) changed (shown above under Reported metrics). These are the numbers the evaluation publishes, and they are recorded independently of the samples.
     evidence: results.scores[].metrics.exact_match.accuracy
2. 1 sample(s) errored in the new run that did not error in the old one (shown under Changed samples). An error suppresses a score, so their contribution to any aggregate metric changed regardless of model behaviour.
     evidence: samples[].error
3. Sample outcomes changed but no recorded configuration field that bears on them changed. The source of this difference is not present in these logs. Candidates these logs would not capture: model-provider nondeterminism, a silently updated model served behind a stable name, and environment differences the log does not record.
     evidence: eval, samples[].scores

inspect-replay compares recorded artifacts. It does not re-run the evaluation; it reports what changed together, not why the result differs. See docs/assurance-boundary.md.
```

The third observation is the point of the tool. Accuracy fell and three samples
moved, but nothing in the recorded configuration accounts for it, so the report
says exactly that instead of blaming a plausible-looking field.

## Why it exists

Comparing two Inspect runs by hand means opening two large archives and reading
nested configuration side by side while trying to remember which sample was
which. The mechanical part of that job (diff the config, align the samples,
classify each sample's fate, rank what moved alongside the result) is what this
tool does. The judgement part, deciding what actually happened, stays with you,
and the tool is built not to pretend otherwise.

Upstream Inspect has no compare or diff feature; the request has been
[open since February 2025](https://github.com/UKGovernmentBEIS/inspect_ai/issues/1327).
See [Prior art](#prior-art) for what does exist and where this fits.

## Who it is for

Evaluation and research engineers who run Inspect and need to answer "why did
this number change" in CI or during debugging: to gate a regression, to explain
a moved metric in a report, or to check that a config edit changed only what it
was meant to.

## Install

Not yet published to PyPI. Install from source.

```console
git clone https://github.com/repowazdogz-droid/inspect-replay
cd inspect-replay
pip install -e ".[dev]"
pytest                       # 117 tests
inspect-replay compare examples/baseline.eval examples/sample-regression.eval
```

Or install the package alone (without the bundled example logs):

```console
pip install git+https://github.com/repowazdogz-droid/inspect-replay
```

Requires Python 3.11 or newer and `inspect_ai>=0.3.180` (the oldest version the
suite is verified against; see [engineering guarantees](#engineering-guarantees)).

## Use

```console
# human-readable report
inspect-replay compare old.eval new.eval

# machine-readable, for CI
inspect-replay compare old.eval new.eval --json -o diff.json

# list every changed sample, not just the first ten
inspect-replay compare old.eval new.eval --verbose
```

### Exit codes

A CI gate reads the exit code. The two failure modes are kept distinct so a gate
can tell "your file is broken" from "your two runs do not line up".

| Code | Meaning |
| --- | --- |
| `0` | no differences |
| `1` | differences found |
| `2` | a log could not be read (missing, malformed, wrong format) |
| `3` | the logs were read but no sample could be aligned |

A regression gate is one line. `--exit-zero` returns the report without failing
the build (it does not suppress `2` or `3`, which are errors, not differences):

```yaml
- run: inspect-replay compare baseline.eval candidate.eval
```

### Python API

```python
from inspect_replay import compare, render, to_dict

result = compare("baseline.eval", "candidate.eval")
print(result.verdict)                 # Verdict.CHANGED
print(result.summary.newly_failing)   # 2
print(render(result))                 # the text report shown above
payload = to_dict(result)             # machine-readable dict, schema_version "0.1"
```

## What it compares

**Configuration.** Task name, version, and args; model, model args, and base URL;
every generation parameter individually (temperature, top_p, seed, and the rest);
prompt text from every plan step that records it; dataset name, location, size,
and sample ids; scorer names and options; solver and plan steps; sandbox; epochs
and limits; installed package versions; git commit and dirty flag.

**Reported metrics.** The numbers the evaluation publishes (`accuracy` and the
rest, from `EvalResults`). These live in the log header, so they are compared
even when the logs carry no samples.

**Samples.** Aligned by the strongest stable key available, then classified as
unchanged, newly passing, newly failing, score changed, mixed, error introduced,
error resolved, errored in both, added, removed, input changed, or
scores-not-comparable. With `epochs > 1` the epoch-reduced score is compared,
because that is what the evaluation's metric is computed from.

**Tools.** Read from `plan.steps[].params["tools"]`, where `use_tools()` records
them. `EvalSpec` has no `tools` field, but that does not make the tool set
unrecorded, and a model quietly gaining a shell between two runs is exactly the
kind of change this should surface.

## Architecture

Two logs in, one `Comparison` out, rendered as text or JSON. Each stage is a
small module with one responsibility. Nothing samples, hashes with a random seed,
or reads the clock, so the same two logs always produce byte-identical output.

```
old.eval ─┐                              ┌─ config_diff.compare_config ──→ FieldDiff[]
          ├─ loader.load_log ─→ EvalLog ─┼─ config_diff.compare_results ─→ FieldDiff[]  (headline metrics)
new.eval ─┘                              │
                                         ├─ align.align_samples ─────────→ Alignment
                                         │        └─ sample_diff (reductions if epochs>1, else rows)
                                         │                    └─ summarize ─→ SampleSummary
                                         └─ compare._observations ───────→ Observation[]
                                                                              │
                                              Comparison ─┬─ report.render ───┘   (text)
                                                          └─ json_output.to_dict  (JSON)
```

| Module | Responsibility |
| --- | --- |
| `loader.py` | Read-only load; turns any failure into a `LoadError` naming the path and reason. |
| `models.py` | The data model, including the ignorance states (`UNKNOWN`, `NOT_CHECKED`, `NOT_COMPARABLE`). |
| `align.py` | Sample correspondence. The riskiest module; content-hash keys, no silent positional fallback. |
| `config_diff.py` | Field-by-field configuration and headline-metric comparison; each diff carries its evidence. |
| `sample_diff.py` | Per-sample classification and the score-reading rules. |
| `compare.py` | Orchestration and the ranked, causation-free observations. |
| `report.py`, `json_output.py` | Text and versioned JSON rendering. |
| `text.py` | Control-character sanitisation for untrusted strings printed to a terminal. |
| `cli.py` | Argument parsing and exit codes. |

The single runtime dependency is `inspect_ai`; logs are read with its own
`read_eval_log`, so the tool tracks the real schema rather than a copy of it.
[docs/design.md](docs/design.md) covers alignment, score semantics, and the
reasoning behind each decision.

## Engineering guarantees

Each item is enforced by a mechanism, not by intention. The named tests live in
`tests/`.

- **Deterministic output.** The same two logs produce byte-identical text and
  JSON. (`test_output_is_deterministic`)
- **Read-only.** The tool never writes to the logs it reads; a test hashes them
  before and after. (`test_comparison_does_not_modify_the_input_logs`)
- **Regression-tested by construction.** Detectors are tested mutation-style:
  each is shown to fire on its intended change and stay silent on unrelated
  changes. Every past defect has a named test in `tests/test_regressions.py`.
- **Terminal-injection hardened.** Control characters in untrusted log content
  are stripped before printing; the JSON path escapes every non-ASCII byte.
  (`tests/test_honesty.py`, ANSI and C1/DEL cases)
- **No causal overclaim.** A build-time detector fails the build if the tool's
  authored prose asserts causation rather than co-occurrence, across a labelled
  scenario for each observation branch.
- **Strict typing.** `mypy --strict` clean; ships `py.typed`.
- **Reproducible dependency floor.** A CI job installs the exact declared minimum
  (`inspect_ai==0.3.180`) and runs the full suite, so the floor cannot silently
  drift. The matrix also runs the latest on Python 3.11, 3.12, and 3.13.
- **Explicit assurance boundary.** Unrecorded fields are `UNKNOWN`, never
  "unchanged"; a comparison that could not be performed is `NOT_COMPARABLE` with
  a distinct exit code, never a false green.

## What it will not tell you

This is the part worth reading before you trust a report. The full version is in
[docs/assurance-boundary.md](docs/assurance-boundary.md).

- **It does not assert causation.** It reads two snapshots and cannot hold a
  variable fixed, so it reports co-occurrence, not cause. The wording is guarded
  at build time (see above). The guard covers text the tool authors, not strings
  it quotes from a log, and it catches wording, not implication.
- **A numeric score change is not a regression.** If a scorer emits 0.9 then 0.4,
  no threshold in the log says what passing means; that is `SCORE_CHANGED`, not
  `newly_failing`. Only `"C"`/`"I"`/`"P"`/`"N"`, booleans, and exactly `1`/`0`
  carry a correctness reading (`"C"` to `"P"` and `"C"` to `"N"` are regressions:
  the answer is no longer correct).
- **Different scorers are not comparable.** `exact_match="C"` and
  `model_graded_qa=0.9` do not measure the same thing; that is
  `SCORES_NOT_COMPARABLE`, not a manufactured regression.
- **A reused sample id with a changed input is not the same sample.** If a dataset
  edits a question but keeps its id, the sample is `INPUT_CHANGED` and its scores
  are not compared.
- **It never aligns by position unless asked.** If no stable key works it reports
  that it could not align the samples rather than guessing.
  `--allow-positional-alignment` forces it and labels every conclusion unreliable.
- **It does not reproduce provider outputs.** "Replay" here means reconstructing
  and comparing the recorded state, not re-running the model. The tool never
  calls a model and never re-runs a task.

## Security model

`inspect-replay` is local, read-only, and offline: no network calls, no
telemetry, no code execution, no sandbox. Its threat surface is parsing an
untrusted `.eval` file and printing values from it.

- **Terminal-control injection is mitigated.** A crafted log cannot inject ANSI
  escapes into the text report to forge a verdict; control characters are
  stripped from every untrusted field, and the JSON path escapes them. Tested.
- **Decompression bombs are not yet bounded.** A crafted `.eval` (a zip) can
  expand to exhaust memory during parsing, upstream in `inspect_ai`/`zipfile`.
  Run under a memory limit for untrusted logs.

Full detail and the reporting process are in [SECURITY.md](SECURITY.md).

## Roadmap

Scope is deliberately fixed: two logs in, one report out. The following are
plausible next steps, in rough priority order. None is committed.

- A pre-parse size guard for decompression-bomb inputs, if it can be done without
  reaching into `inspect_ai` internals.
- Comparison of more than two logs (a run series), if the alignment story stays
  honest across N.
- Publication to PyPI once the API and JSON schema are frozen at 1.0.

Out of scope, on purpose: re-running evaluations, model calls, a web UI, LLM
judging of differences, statistical significance testing (use `inspect-mlflow`).

## Prior art

`inspect_ai` itself has no compare or diff feature
([#1327](https://github.com/UKGovernmentBEIS/inspect_ai/issues/1327), open since
February 2025). [`inspect-mlflow`](https://github.com/debu-sinha/inspect-mlflow)
does provide sample-aligned regression analysis with statistical testing this
tool does not attempt; if that is what you need, use it. It has no configuration
diff, no CLI, and no machine-readable output, which is where `inspect-replay`
sits. The full premise check, including the argument for not building this, is in
[docs/prior-art.md](docs/prior-art.md).

## Contributing

Contributions are welcome, especially any case where the tool told you something
misleading. See [CONTRIBUTING.md](CONTRIBUTING.md) for the setup and the four
non-negotiable rules (never encode unknown as "unchanged"; never report
"unchanged" for a comparison that did not happen; never assert causation; never
align by position silently), each backed by a test.

## Documentation

- [Assurance boundary](docs/assurance-boundary.md): what it establishes and what it cannot.
- [Technical design](docs/design.md): architecture, alignment, score semantics, and rationale.
- [Prior art](docs/prior-art.md): what already exists and where this fits.
- Example [text report](docs/example-report.txt) and [JSON report](docs/example-report.json).

## Citation

If you reference this work, see [CITATION.cff](CITATION.cff) or use GitHub's
"Cite this repository" button.

## License

MIT, © 2026 Warren Smith. See [LICENSE](LICENSE).
