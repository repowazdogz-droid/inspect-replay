# Technical design

## Problem

An evaluation result moved between two runs. You have two `.eval` logs and a
question: what changed? Answering it by hand means opening two large JSON-ish
archives and eyeballing nested config while trying to remember which sample was
which.

The mechanical part of that job is: diff the configuration, put the samples into
correspondence, classify each sample's fate, and rank what moved alongside the
result. That is what this tool does. The judgement part — deciding what actually
happened — stays with the human, and the tool is built not to pretend otherwise.

## Pipeline

```
old.eval ─┐                              ┌─ config_diff.compare_config ──→ FieldDiff[]
          ├─ loader.load_log ─→ EvalLog ─┼─ config_diff.compare_results ─→ FieldDiff[]  (headline metrics)
new.eval ─┘                              │
                                         ├─ align.align_samples ─────────→ Alignment
                                         │        │
                                         │        ├─ sample_diff.compare_reductions ─→ SampleDiff[]  (if epochs>1)
                                         │        └─ sample_diff.compare_samples ────→ SampleDiff[]
                                         │                    │
                                         │                    └─ sample_diff.summarize ─→ SampleSummary
                                         │
                                         └─ compare._observations ───────→ Observation[]
                                                                              │
                                              Comparison ─┬─ report.render ───┘  (text)
                                                          └─ json_output.to_dict  (JSON)
```

| Module | Responsibility |
| --- | --- |
| `loader.py` | Read-only load. Turns any failure into a `LoadError` naming the path and the reason. |
| `models.py` | The data model, including the ignorance states. No logic. |
| `align.py` | Sample correspondence. The riskiest module; see below. |
| `config_diff.py` | Field-by-field configuration comparison, plus the headline metrics from `EvalResults`. Each diff carries its evidence. |
| `sample_diff.py` | Per-sample classification and the score-reading rules. |
| `compare.py` | Orchestration and the ranked observations. |
| `report.py` | Text rendering. |
| `json_output.py` | Versioned machine-readable rendering. |
| `text.py` | Control-character sanitisation for untrusted strings printed to a terminal. |
| `cli.py` | Argument parsing and exit codes. |

## The data model encodes ignorance

```python
class Status(StrEnum):
    SAME = "SAME"
    CHANGED = "CHANGED"
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    UNKNOWN = "UNKNOWN"        # the schema has this field; the log did not record it
    NOT_CHECKED = "NOT_CHECKED"  # this tool does not compare it, and says why
```

`UNKNOWN` and `NOT_CHECKED` are load-bearing. A field absent from both logs is
`UNKNOWN`, not `SAME`: we did not observe that it was identical, we observed
nothing. Encoding that absence as `SAME`, or as `false`, would hand a consumer a
fact the data does not support.

`Verdict` carries the same discipline at the top level:

```python
class Verdict(StrEnum):
    UNCHANGED = "UNCHANGED"
    CHANGED = "CHANGED"
    NOT_COMPARABLE = "NOT_COMPARABLE"   # exit code 3
```

`NOT_COMPARABLE` exists because the exit code is what a CI gate acts on. If no
sample could be aligned — `log_samples=False`, a truncated run, no matching key —
then reporting `UNCHANGED` and exiting 0 passes the gate on an evaluation nobody
looked at. An earlier version of this tool did exactly that. It is now exit 3
(distinct from exit 2, an unreadable log),
and `--exit-zero` does not suppress it.

The headline metrics in `EvalResults` are compared regardless of samples, because
they live in the log header and survive `log_samples=False`. That is the case
where a samples-only tool reports "unchanged" on a run whose accuracy fell from
0.92 to 0.21.

### Tools: an instructive mistake

An earlier version printed `tools: NOT_CHECKED — Inspect does not record the
configured tool set`. `EvalSpec` indeed has no `tools` field, so the premise was
true — but the inference was false. `use_tools()` records the tool set in
`plan.steps[].params["tools"]`, and the plan is part of the header. The tool told
users that a change they would very much care about (a model being handed a
shell) was undetectable, while the data sat in the log it had open.

Tools are now read from the plan. Where no plan step records them, the status is
`UNKNOWN` — "no plan step recorded tools", not "no tools were used", and
certainly not "Inspect cannot record tools". Tested in
`tests/test_regressions.py`.

## Alignment

Alignment is where a diff tool can be confidently, invisibly wrong. Match two
samples that are not the same sample, and every regression you report afterwards
is fiction that reads as fact.

Keys, strongest first:

1. `(sample id, epoch)`
2. `sha256(input, target, choices) + epoch` — when ids are missing, duplicated,
   or share no values across the two logs
3. position in the list — **only** with `--allow-positional-alignment`

A key that is not unique within a log is rejected rather than used to collide
samples together. A key that produces zero overlap between the two logs is
skipped rather than used to declare every sample simultaneously added and
removed.

If nothing works and positional alignment was not requested, the tool reports
`AlignmentMethod.NONE`, aligns nothing, and counts every sample as unalignable.
It does not guess. Unalignable samples are counted in `unalignable_old` /
`unalignable_new` and are deliberately **not** folded into `added` / `removed`,
because "we could not match this" and "this was deleted" are different facts.

The input fingerprint hashes only the input, target, and choices — never the
output, scores, or timings. The key must not depend on the things being compared.

**It also strips volatile identifiers, and this is not a detail.** Inspect's
`ChatMessage` carries an `id` that defaults to a fresh uuid on every
construction. An earlier version hashed a naive `model_dump()` of the input, so
two runs of the *identical* chat-format dataset produced a different fingerprint
for every sample — and every real regression came out as "the input changed",
excluded from the counts. Chat-format inputs are the normal case for agentic and
safety evals, so this quietly disabled the tool on exactly the evaluations it is
meant for. `VOLATILE_KEYS` in `align.py` is what stops it; it is tested directly
in `tests/test_regressions.py`.

### The id-reuse trap

The nastiest silent failure is a dataset that edits a question and keeps its id.
Id-based alignment matches the two samples, and their scores differ, and the tool
would report a regression — comparing an answer to one question against an answer
to a different question.

So every aligned pair is checked: if the input fingerprints differ, the sample is
classified `INPUT_CHANGED`, its scores are **not** compared, and it is excluded
from the pass/fail counts.

This also catches the id-swap case: if two samples exchange ids but keep their
inputs, both fingerprints move relative to their new partners, and both are
reported `INPUT_CHANGED` rather than as a matched pair of regressions.

What it cannot catch is a dataset that changes a sample's *meaning* without
changing its recorded input or target — a target silently corrected, say, where
the log records the new target in both runs.

## Reading a score

Inspect score values may be `"C"`/`"I"`/`"P"`/`"N"`, a bool, a number, a list, or
a dict. Only some carry a binary reading, and the tool only claims one where it
exists:

| Value | Reading | Numeric |
| --- | --- | --- |
| `"C"` | PASS | 1.0 |
| `"I"` | FAIL | 0.0 |
| `"P"` | PARTIAL | 0.5 |
| `"N"` | NO_ANSWER | 0.0 |
| `True` / `False` | PASS / FAIL | 1.0 / 0.0 |
| exactly `1` / `0` | PASS / FAIL | 1.0 / 0.0 |
| any other number | **UNKNOWN** | as-is |
| list, dict | **UNKNOWN** | — |

A scorer emitting 0.9 and then 0.4 has produced a real, reportable change — but
the log contains no threshold saying what "passing" is, so calling it a
regression would be the tool inventing a criterion. It is reported as
`SCORE_CHANGED` with both magnitudes, counted separately, and excluded from
`newly_failing`.

A regression is a crossing of the **correct / not-correct** boundary, not merely
of PASS/FAIL. `"C"` → `"P"` and `"C"` → `"N"` are regressions: the answer is no
longer correct and Inspect's own accuracy metric drops. An earlier version
admitted only PASS and FAIL to the flip check, so a model that stopped answering
was filed under "no pass/fail reading defined" — a bucket a regression-hunting
reader skips.

A scorer that re-encodes `"C"` as `1.0` has not changed its verdict, and
`ScoreDiff.equivalent` keeps that out of the change counts.

When several scorers move in opposite directions on one sample — safety down,
capability up — the outcome is `MIXED`. Picking one direction would let the
summary assert `newly_passing == 0` while a scorer newly passed.

### Epochs

With `epochs > 1` a dataset sample produces several rows, and the score the
evaluation reports is the epoch-**reduced** score (`EvalLog.reductions`). Where
both logs record reductions, those are compared. A sample scored `[C,C,C,C]` then
`[C,I,I,I]` under a `max` reducer has not regressed and the eval's accuracy does
not move; comparing raw epoch rows would report three regressions in a number no
metric reads.

Where reductions are absent, raw epoch rows are compared and `summary.unit`
becomes `"sample-epoch row"` — because calling four rows four samples reports a
denominator that does not exist in the dataset.

Only scorers present on **both** sides measure a comparable quantity. If the two
runs share no scorer, the outcome is `SCORES_NOT_COMPARABLE`: comparing
`exact_match="C"` with `model_graded_qa=0.9` is comparing two different
measurements, and any "regression" between them is an artifact of the tool.

## Observations, not causes

`compare._observations` ranks what changed alongside the outcome. The ranking is
by how directly a field bears on the recorded outcome — dataset, then scorer,
then model, then prompt, then solver, then generation parameters, then sandbox,
then errors. It is not an effect-size ordering; nothing here is measured.

Each observation carries the log fields it was derived from. The JSON marks
every one with `"relationship": "co-occurrence"`.

The case worth building for is the one where **nothing in the configuration
changed and the outcomes moved anyway**. The tool says so plainly, and names the
things these logs would not have captured: provider nondeterminism, a model
updated behind a stable name, environment differences. That is a more useful and
more honest answer than a fabricated cause.

## Determinism

Config fields are emitted in a fixed order. Samples are sorted by alignment key.
JSON is `sort_keys=True`. Nothing samples, hashes with a random seed, or reads
the clock. `test_output_is_deterministic` asserts byte-identical output across
repeated runs.

## Exit codes

`0` no differences · `1` differences found · `2` comparison failed.

This makes the tool usable as a CI gate. `--exit-zero` forces `0` on a successful
comparison for pipelines that want the report without failing the build.

## Dependencies

One: `inspect_ai`. The CLI is stdlib `argparse`. The logs are read with
Inspect's own `read_eval_log`, so the tool tracks the real schema rather than a
reimplementation of it.

## Deliberately not built

No web UI, no distributed execution, no re-running of evaluations, no model
calls, no LLM judging of differences, no trajectory visualisation, no experiment
platform. The tool does one job.
