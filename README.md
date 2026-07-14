# inspect-replay

**inspect-replay compares two [Inspect AI](https://inspect.aisi.org.uk)
evaluation logs and shows what changed in configuration, samples, scores, and
errors.**

You have two `.eval` logs and a result that moved. This tells you what is
different between them: which config fields changed, which samples regressed,
which recovered, which errored, and which could not be compared at all.

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
1. The evaluation's own reported metrics changed (exact_match.accuracy: 0.75 to 0.5). These are the numbers the evaluation publishes, and they are recorded independently of the samples.
     evidence: results.scores[].metrics.exact_match.accuracy
2. 1 sample(s) errored in the new run that did not error in the old one (q4::1: ToolError: bash sandbox exited with code 137 (out of memory)). An error suppresses a score, so their contribution to any aggregate metric changed regardless of model behaviour.
     evidence: samples[].error
3. Sample outcomes changed but no recorded configuration field that bears on them changed. The source of this difference is not present in these logs. Candidates these logs would not capture: model-provider nondeterminism, a silently updated model served behind a stable name, and environment differences the log does not record.
     evidence: eval, samples[].scores

inspect-replay compares recorded artifacts. It does not re-run the evaluation and cannot show that a configuration change produced an outcome change. See docs/assurance-boundary.md.
```

The last observation is the point of the tool. Accuracy fell, three samples
moved, and **nothing in the recorded configuration accounts for it** — so the
report says exactly that, instead of picking a plausible-looking field and
blaming it.

## Install

```console
pip install inspect-replay
```

Or from a clone:

```console
git clone https://github.com/repowazdogz-droid/inspect-replay
cd inspect-replay
pip install -e ".[dev]"
pytest
```

Requires Python 3.11+ and `inspect_ai`.

## Use

```console
# human-readable report
inspect-replay compare old.eval new.eval

# machine-readable, for CI
inspect-replay compare old.eval new.eval --json -o diff.json

# every changed sample, not just the first 10
inspect-replay compare old.eval new.eval --verbose
```

Exit codes: **0** no differences · **1** differences found · **2** the comparison
could not be performed (bad path, malformed log, or no sample could be aligned).
So a regression gate is one line:

```yaml
- run: inspect-replay compare baseline.eval candidate.eval
```

Use `--exit-zero` if you want the report without failing the build.

### Python API

```python
from inspect_replay import compare, render, to_dict

result = compare("baseline.eval", "candidate.eval")
print(result.verdict)                      # Verdict.CHANGED
print(result.summary.newly_failing)        # 2
print(render(result))                      # the text report above
payload = to_dict(result)                  # schema_version "1.0"
```

## What it compares

**Configuration** — task name, version, and args; model, model args, and base
URL; every generation parameter individually (temperature, top_p, seed, …);
prompt/system message where recorded; dataset name, location, size, and sample
ids; scorer names and options; solver and plan steps; sandbox; epochs and
limits; installed package versions; git commit and dirty flag.

**Reported metrics** — the numbers the evaluation publishes (`accuracy` and
friends, from `EvalResults`). These live in the log header, so they are compared
even when the logs carry no samples.

**Samples** — aligned by the strongest stable key available, then classified as
unchanged, newly passing, newly failing, score changed, mixed, error introduced,
error resolved, errored in both, added, removed, input changed, or
scores-not-comparable. With `epochs > 1`, the epoch-**reduced** score is what gets
compared, because that is what the evaluation's metric is computed from.

**Tools** — read from `plan.steps[].params["tools"]`, where `use_tools()` records
them. `EvalSpec` has no `tools` field, but that does not mean the tool set is
unrecorded, and a model quietly gaining a shell between two runs is exactly the
kind of change this should surface.

## What it refuses to tell you

This is the part worth reading before you trust a report.

**It never says "caused by."** It reads two snapshots. If the temperature
changed and the score changed, it saw those two things co-occur — it did not run
the evaluation with the temperature held fixed, so it cannot tell you which
change mattered. Observations are worded as "changed alongside" and "possible
contributor", and a test fails the build if causal wording ever appears in
generated output.

**A numeric score change is not a regression.** If a scorer emits 0.9 and then
0.4, no threshold in the log says what passing means. That is reported as
`SCORE_CHANGED`, not `newly_failing`. Only `"C"`/`"I"`/`"P"`/`"N"`, booleans, and
exactly `1`/`0` carry a correctness reading. (`"C"` → `"P"` and `"C"` → `"N"` **are**
regressions: the answer is no longer correct, and Inspect's accuracy metric falls.)

**Scorers moving in opposite directions is not a regression.** If a sample's
safety scorer worsens while its capability scorer improves, calling that sample a
regression throws away half the evidence. It is reported as `MIXED` and counted as
neither.

**Different scorers are not comparable.** `exact_match="C"` and
`model_graded_qa=0.9` do not measure the same thing. If the two runs share no
scorer, the tool reports `SCORES_NOT_COMPARABLE` rather than manufacturing
regressions.

**A reused sample id with a changed input is not the same sample.** If a dataset
edits a question but keeps its id, the tool reports `INPUT_CHANGED` and does not
compare the scores — otherwise it would be comparing answers to different
questions.

**It never aligns by position unless you ask.** If no stable key works, it tells
you it could not align the samples rather than guessing and being confidently
wrong. `--allow-positional-alignment` forces it, and then labels every
conclusion unreliable.

**`UNKNOWN` is not `unchanged`.** Where a log records nothing, the report says
`UNKNOWN`, not "unchanged". Reporting an unrecorded field as identical is a
falsehood you might act on.

**It will not tell you nothing changed when it compared nothing.** If no sample
could be aligned — a log written with `log_samples=False`, a truncated run — the
verdict is `NOT_COMPARABLE` and the exit code is 2, never 0. A tool that returns
"unchanged" for an evaluation it never looked at will pass a CI gate on a run
that collapsed. Headline metrics from `results` are still compared, because they
live in the header and survive `log_samples=False`.

"Replay" here means reconstructing and comparing the **recorded** evaluation
state. inspect-replay never calls a model, never re-runs a task, and cannot
reproduce provider outputs. Full boundary:
[docs/assurance-boundary.md](docs/assurance-boundary.md).

## Prior art

`inspect_ai` itself has no compare/diff feature
([#1327](https://github.com/UKGovernmentBEIS/inspect_ai/issues/1327) has been
open since Feb 2025). [`inspect-mlflow`](https://github.com/debu-sinha/inspect-mlflow)
**does** provide sample-aligned regression analysis, with statistical testing
this tool does not attempt — if that is what you need, use it. It has no
configuration diff, no CLI, and no JSON output, which is where inspect-replay
sits. The full premise check is in [docs/prior-art.md](docs/prior-art.md),
including the case for *not* building this.

## Docs

- [Assurance boundary](docs/assurance-boundary.md) — what it establishes and what it cannot
- [Technical design](docs/design.md) — architecture, alignment, score semantics
- [Prior art](docs/prior-art.md) — what already exists
- [Example text report](docs/example-report.txt) · [Example JSON report](docs/example-report.json)

## Examples

`examples/` holds four `.eval` logs written with Inspect's own writer
(`write_eval_log`), plus a deliberately malformed file. They are synthetic — no
model was called — and each isolates one difference from the baseline:

| Log | Differs from baseline by |
| --- | --- |
| `model-change.eval` | model name, and the completions that followed |
| `scorer-change.eval` | scorer swapped for one with no shared score type |
| `sample-regression.eval` | nothing in the config; only sample outcomes moved |
| `malformed.eval` | not a valid log, for the error path |

Regenerate them with `python examples/generate_examples.py`. CI checks they are
reproducible from the committed generator.

## Status

v0.1.0. Scope is deliberately fixed: two logs in, one report out. No web UI, no
experiment platform, no re-running of evaluations.

## Licence

MIT.
