# Prior art and premise check

Before writing any code, the premise of this tool was checked against the
alternatives: if an actively maintained tool already provided sample-aligned
Inspect run comparison, configuration diff, outcome regression analysis, and
machine-readable reports, this project should not exist.

**Verdict: the premise survived, but it forced a narrower scope than originally
planned.** Roughly half of the original idea was already built by someone else.
This page records what exists, so that a reader can judge for themselves.

Checked on 2026-07-13 against `inspect_ai` 0.3.246.

## What already exists

### inspect-mlflow — does the outcome-diff half, and does it well

[`inspect-mlflow`](https://github.com/debu-sinha/inspect-mlflow)
([PyPI](https://pypi.org/project/inspect-mlflow/)) is maintained (v0.8.0,
June 2026) and its `inspect_mlflow.comparison` module exposes
`compare_evals(baseline, candidate)`.

Reading its source rather than its README, it provides:

- sample alignment on `(id, epoch)`, with string/int id normalisation and
  multi-epoch support
- outcome regression analysis: `improved` / `regressed` / `unchanged` / `new` /
  `missing`
- statistical significance work that this project does not attempt: McNemar's
  test, bootstrap confidence intervals, Cohen's d, win rate

**If you want statistically-tested regression analysis of two Inspect runs, use
inspect-mlflow.** It is better at that than inspect-replay, and inspect-replay
does not try to compete with it. This project does not reimplement its
statistics, and claims no novelty for sample-aligned outcome comparison.

What it does not have, verified by reading `inspect_mlflow/comparison/`:

- **no configuration diff.** Its `ComparisonResult` carries `baseline_model`,
  `candidate_model`, `baseline_task`, `candidate_task` and nothing else. There
  is no comparison of temperature, generation config, scorer configuration,
  dataset identity, solver, sandbox, packages, or git revision.
- **no machine-readable output.** The public surface is `summary() -> str`, a
  formatted text table. There is no `to_dict`, `to_json`, or `model_dump` in
  the comparison module.
- **no CLI.** Its entry points register an `inspect_ai` hook, not a
  `console_scripts` command.
- **no errored-sample category.** Errors are not a distinct outcome.
- it pulls in MLflow, which is a large dependency if all you want is a diff.

### Inspect AI itself — no comparison feature

`inspect_ai` 0.3.246 has no diff or compare capability. Its `inspect log`
subcommands are `list`, `dump`, `convert`, `headers`, `schema`, `types`,
`export-config`, and `recover`. `inspect score` re-scores a single log and
`inspect eval-retry` re-runs a single task; neither compares two runs. The
`inspect_ai.analysis` dataframe API (`evals_df`, `samples_df`) gives you the
raw material to build a comparison, but not a comparison.

Comparison has been requested and is unbuilt:

- [#1327 "Side by side comparison of two models"](https://github.com/UKGovernmentBEIS/inspect_ai/issues/1327),
  open since February 2025. A maintainer: *"Right now there isn't an automated
  way to do this (though it is on our todo list)"*. A commenter asks
  specifically to compare two evals with different sampling parameters and vLLM
  versions, which is the configuration diff this tool provides.
- [#4206](https://github.com/UKGovernmentBEIS/inspect_ai/issues/4206), open,
  on an eval-reliability toolkit: *"the questions people actually act on are
  comparisons."*

**Upstream may build this.** Maintainers have said comparison is on the todo
list. If Inspect ships a first-party comparison feature that covers
configuration diff, this tool's reason to exist shrinks accordingly, and the
honest thing will be to say so here rather than to keep it alive.

### Adjacent, but solving a different problem

- [`inspect-judge-drift`](https://github.com/avalyset/inspect-judge-drift)
  re-grades *one* stored log with *two* graders, isolating the judge. It is not
  a two-run comparison.
- `inspect-viz` visualises logs; it does not compare two of them.
- Generic JSON diff tools (`jd`, `deepdiff`, `git diff`) will happily diff two
  `.eval` files. They cannot align samples, cannot tell a score regression from
  a reordered list, and produce output no one wants to read. **The existence of
  generic diff tools was explicitly not treated as grounds to kill this
  project**, but it is also not a reason to build something they already do:
  the value here is in alignment and in the specific claims the tool refuses to
  make.
- Braintrust, Langfuse, and Weave have eval comparison features. They are not
  Inspect-native and require adopting their platform.

## What did not exist, and is what this tool is for

1. A **configuration diff** across two `.eval` logs: model, generation
   parameters field by field, prompt/system message, dataset identity and size,
   scorer name and options, solver and plan steps, sandbox, run config, package
   versions, git revision.
2. A **CLI** for two-log comparison, with exit codes that CI can act on.
3. **Machine-readable JSON output** with a versioned schema.
4. Explicit **`UNKNOWN` states** where the log records no evidence, and a
   **`NOT_COMPARABLE` verdict** (exit code 2) when no sample could be aligned,
   rather than silently reporting "unchanged".

Point 4 is the one that matters most and is the least glamorous. A comparison
tool that reports "unchanged" for an evaluation whose samples it never read will
pass a CI gate on a run that collapsed. inspect-replay refuses to, and compares
the headline metrics from `results` — which live in the log header and survive
`log_samples=False` — so a dropped accuracy is visible even when no sample is.

## Honest positioning

inspect-replay is **the configuration diff, CLI, and JSON output that
inspect-mlflow's comparison does not have**, plus a sample comparison that is
deliberately conservative about what a score change licenses you to conclude.

It is not novel in the sense of doing something no one has done. It is a small,
careful tool for a job that is currently done by reading two log files side by
side.
