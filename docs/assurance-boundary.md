# Assurance boundary

What this tool establishes, and what it does not. Read this before you act on
one of its reports.

## What "replay" means here

**"Replay" in inspect-replay means reconstructing and comparing the recorded
evaluation state. It does not mean reproducing model-provider outputs.**

inspect-replay never calls a model, never runs a task, never touches a sandbox,
and never re-scores anything. It reads two files that already exist and tells
you how they differ. If you delete your API keys and unplug the network, it
works exactly the same.

## What it does

- Compares two recorded Inspect `.eval` artifacts.
- Detects differences in recorded configuration: task, model, model arguments,
  generation parameters, prompt/system message where recorded, dataset identity
  and size, scorer names and options, solver and plan steps, sandbox, run
  config, package versions, git revision.
- Aligns recorded samples using the strongest stable key available, and says
  which key it used.
- Compares the headline metrics the evaluation reports (`EvalResults`), which
  live in the log header and are available even when samples are not.
- Classifies aligned samples as unchanged, newly passing, newly failing, score
  changed, mixed, error introduced, error resolved, errored in both, added,
  removed, input changed, or not comparable.
- Compares the configured tool set where a plan step records it.
- Reports which samples could not be aligned at all, and does not hide them.
- Produces deterministic output: the same two logs give byte-identical reports.
- Ranks what changed alongside the observed result, naming the log fields each
  statement was derived from.

## What it does not do

- **It does not prove why model behaviour changed.** It has one observation of
  each configuration, cannot hold a variable fixed, and cannot re-run anything.
  Two things changing together in a record is not evidence that one produced the
  other.
- **It does not reproduce remote API outputs.** Model generation happens on a
  provider's servers. A recorded completion cannot be re-derived from the log.
- **It does not prove an evaluation is valid.** Two runs can agree perfectly and
  both be measuring the wrong thing.
- **It does not detect unrecorded configuration.** If a change is not written
  into the `.eval` log, this tool cannot see it. Environment variables, a model
  silently updated behind a stable name, provider-side sampling changes, machine
  differences, and the actual contents of a dataset file are all invisible here.
  Tools are a subtle case: `EvalSpec` has no `tools` field, but `use_tools()`
  records the tool set in `plan.steps[].params["tools"]`, so inspect-replay reads
  it from there. A solver that configures tools **without** recording them in its
  plan params is invisible, and the report says `UNKNOWN` — meaning "no plan step
  recorded tools", not "no tools were used".
- **It does not verify dataset truth.** It compares dataset *identity* (name,
  location, size, sample ids). It does not check that the targets are correct or
  that the file behind a location is the same file.
- **It does not establish semantic equivalence.** Two differently-worded prompts
  may mean the same thing; two identical completions may mean different things
  in context. This tool compares recorded strings, not meanings.
- **It does not infer causation from temporal association.** See below.

## Why the observations are worded the way they are

The report's ranked observations use, and are restricted to, phrasing like:

- "changed alongside"
- "possible contributor"
- "cannot be determined from the recorded data"

They never say "caused by". This is enforced by a test
(`tests/test_honesty.py::test_no_causal_wording_in_any_generated_report`) that
fails the build if causal vocabulary appears in any generated report, across
every scenario the tool can produce.

The reason is structural, not stylistic. To establish that a temperature change
caused a regression you would need to re-run the evaluation with the temperature
held at its old value and observe the regression disappear. inspect-replay
cannot do that. It sees two snapshots. In those snapshots, the temperature is
different and the score is different. That is co-occurrence. Two configuration
fields often change together in a real commit, and the report cannot tell you
which of them mattered — it can only tell you both moved.

The ranking of observations reflects **how directly a changed field bears on the
recorded outcome**, not any measured effect size. A scorer change is ranked
above a package-version change because a scorer directly computes the outcome,
not because the tool measured that it mattered more. It is an ordering of
candidates for a human to investigate.

## It will not say "unchanged" when it compared nothing

If no sample could be aligned — because a log was written with
`log_samples=False`, because a run was truncated, or because no stable key
matched — the verdict is **`NOT_COMPARABLE`** and the exit code is **2**. It is
never `UNCHANGED` and never exit 0.

This matters because the exit code is what a CI gate acts on. A tool that
returns "no differences" for an evaluation whose samples it never read will pass
the gate on a run that collapsed. `--exit-zero` does not suppress this: a
comparison that could not be performed is an error, not a difference.

The headline metrics in `EvalResults` are compared regardless, because they live
in the log header and survive `log_samples=False`. So a run whose accuracy fell
from 0.92 to 0.21 is reported as changed even when not one sample is available
to explain it.

## The refusals that matter

Four cases where the tool declines to give you a number, because the number
would be an artifact of the tool rather than a fact about the evaluation:

1. **A numeric score change is not a pass/fail flip.** If a scorer emits 0.9 and
   then 0.4, the log records no threshold that says what passing means. The tool
   reports `SCORE_CHANGED` with the magnitudes and does **not** count it as a
   regression. Only `"C"`/`"I"`/`"P"`/`"N"`, booleans, and exactly `1`/`0` carry
   a binary reading.
2. **Disjoint scorers are not comparable.** If the old run scored with
   `exact_match` and the new run scored with `model_graded_qa`, there is no
   common quantity. `"C"` and `0.9` do not measure the same thing. The tool
   reports `SCORES_NOT_COMPARABLE` and refuses to call anything a regression.
3. **A reused id with a changed input is not the same sample.** If a dataset
   edits a question but keeps its id, comparing the two scores compares answers
   to different questions. The tool reports `INPUT_CHANGED` and excludes the
   sample from the pass/fail counts.
4. **Scorers moving in opposite directions is not a regression.** If a sample's
   safety scorer worsens while its capability scorer improves, calling the sample
   a regression discards half the evidence. The tool reports `MIXED` and counts
   it as neither.

A related boundary: with `epochs > 1`, the score an evaluation reports is the
epoch-**reduced** score. Where both logs record reductions, those are what get
compared — a sample scored `[C,C,C,C]` then `[C,I,I,I]` under a `max` reducer has
not regressed, and the eval's accuracy does not move. Where reductions are absent
the tool compares raw epoch rows and says so, labelling the unit
`sample-epoch row` rather than calling four rows four samples.

## Alignment is the weakest link, and is labelled as such

If two samples are matched that are not the same sample, every downstream
statement is wrong and looks completely plausible. So:

- Alignment uses the strongest stable key available — sample id and epoch, then
  a content hash of input, target, and choices. The hash strips volatile
  identifiers: Inspect's `ChatMessage` carries a fresh uuid on every
  construction, so hashing a naive dump of a chat input would make two runs of
  the identical dataset look like two different datasets.
- **Positional alignment is never used unless you explicitly pass
  `--allow-positional-alignment`.** If no stable key works, the tool reports
  that it could not align the samples rather than guessing. When positional
  alignment is used, every sample carries `alignment_is_weak: true` and the
  report leads with a warning.
- The method actually used is always stated in the report and in the JSON.

## Denominators

A count of regressions without a denominator is not actionable. Every report
states the number of samples in each log, the number aligned, and the number
that could not be aligned. Samples that could not be aligned are never quietly
folded into the added/removed counts.

## Trusted base

Conclusions from this tool are only as good as:

- **the Inspect log itself.** If Inspect did not record it, this tool cannot see
  it. If Inspect recorded it wrongly, this tool faithfully compares the wrong
  thing.
- **`inspect_ai`'s reader.** Logs are parsed with `inspect_ai.log.read_eval_log`.
  A schema change upstream could change what is compared.
- **the assumption that sample ids are stable.** If your dataset reassigns ids
  between runs, id-based alignment pairs the wrong samples. The `INPUT_CHANGED`
  check catches this whenever the input moved with the id — including a clean
  id-swap between two samples, where both are reported `INPUT_CHANGED` rather
  than as a pair of regressions. What it cannot catch is a change to a sample's
  meaning that leaves the recorded input and target identical.
