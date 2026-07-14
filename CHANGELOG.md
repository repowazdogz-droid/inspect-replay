# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-07-14

Correctness, security, and honesty hardening. Every item below closes a defect
found by an adversarial review of 0.1.0; each is guarded by a named regression
test in `tests/`.

### Security

- **Terminal-injection hardening.** An `.eval` log is untrusted input, and the
  text report prints strings taken from it. The report now strips control
  characters from every untrusted string before printing — values *and* the
  identifiers that carry untrusted substrings (error messages, score values,
  model names, dataset/scorer/package names, sample ids, the log location) — via
  a single sanitising funnel (`inspect_replay.text.sanitize`). Without this, a
  crafted log could emit ANSI/OSC escapes to clear the screen and print a forged
  `Evaluation: UNCHANGED` line.
- **JSON control-byte escaping.** `to_json` now uses `ensure_ascii=True`.
  `json.dumps(ensure_ascii=False)` escapes only the C0 range (through ESC) and
  passes DEL (`0x7f`) and the C1 range (`0x80`–`0x9f`, including the C1 CSI
  `0x9b`) through raw; escaping every non-ASCII byte closes that path.
- `SECURITY.md` now documents terminal injection and the decompression-bomb
  exposure during parsing (upstream in `inspect_ai`/`zipfile`), with a
  memory-limit mitigation for untrusted logs.

### Changed

- **Dependency floor raised to `inspect_ai>=0.3.180`**, the oldest release the
  full suite is verified green against. Below `0.3.171` the reader cannot open
  the example logs. A CI job installs this exact floor and runs the suite so it
  cannot silently drift. (0.1.0 declared `>=0.3.100`, which was never tested and
  does not work.)
- **Exit codes split** so a CI gate can distinguish the two failure modes:
  `2` a log could not be read, `3` the logs were read but no sample could be
  aligned. (0.1.0 used `2` for both.)
- **`schema_version` set to `0.1`** to signal the JSON schema is pre-1.0 and not
  frozen. The machine contract (enum values, numeric counters, evidence fields)
  is stable within a version; prose fields (`observations[].statement`, notes)
  and the opaque `sample[].key` are not. See `json_output.py`.
- **Causal-language guard rewritten.** The build-time check that stops the tool
  asserting causation now matches causal verbs and constructions rather than a
  fixed substring list (the old list passed "caused the", "led to", "triggered",
  "drove"), and runs over the tool's *authored* prose across a labelled scenario
  for each observation branch. The tool no longer splices untrusted log content
  (error strings, model names, metric/generation values, sample ids, scorer
  names) into its own sentences; those are shown in their report sections
  instead. `docs/assurance-boundary.md` states what the check does and does not
  cover.

### Fixed

- **Chat-format inputs no longer look like changed questions.** Sample alignment
  hashes input *content*, stripping the per-construction random uuid Inspect
  assigns each `ChatMessage`. Previously two runs of an identical chat-format
  dataset hashed differently for every sample, so real regressions were reported
  as `INPUT_CHANGED` and dropped from the counts.
- **A comparison that could not be performed no longer reports `UNCHANGED`.**
  When no sample aligns (a log written with `log_samples=False`, a truncated
  run), the verdict is `NOT_COMPARABLE` and the exit code is `3`, never
  `UNCHANGED`/`0` — which would have passed a CI gate on a collapsed run.
  Headline metrics from `EvalResults` are compared regardless, because they live
  in the log header and survive `log_samples=False`.
- **Tools are detected.** `EvalSpec` has no `tools` field, but `use_tools()`
  records the tool set in `plan.steps[].params["tools"]`, so a model gaining a
  tool between runs is now surfaced. 0.1.0 wrongly reported tools as
  undetectable.

## [0.1.0] - 2026-07-14

First release: the feature set.

### Added

- `inspect-replay compare OLD NEW` — compares two Inspect `.eval` logs and
  reports what changed in configuration, headline metrics, samples, scores, and
  errors.
- **Configuration diff** across task, model, model args, every generation
  parameter individually, prompt text from every plan step that records it,
  dataset identity and size, scorer names and options, solver and plan steps,
  sandbox, run config, installed package versions, and git revision.
- **Headline-metric comparison** (`EvalResults`), available even when the logs
  record no samples.
- **Sample alignment** on the strongest stable key available — `(id, epoch)`,
  then a content hash of input, target, and choices. Positional alignment exists
  only behind `--allow-positional-alignment` and is labelled weak.
- **Epoch-reduced comparison**: where both logs record `reductions` (epochs > 1),
  the epoch-reduced score is compared — the score the evaluation's metric is
  computed from. Otherwise raw epoch rows are compared and the unit is labelled
  `sample-epoch row`.
- **Sample classification**: unchanged, newly passing, newly failing, score
  changed, mixed, error introduced, error resolved, errored in both, added,
  removed, input changed, scores not comparable, unknown.
- **Ranked observations** naming the log fields each was derived from, worded as
  co-occurrence rather than causation.
- Human-readable text report and machine-readable JSON output.
- Four example `.eval` logs written with Inspect's own writer, plus a malformed
  file, and a committed generator that reproduces them.

### Design commitments

Unrecorded information is `UNKNOWN`, never "unchanged". A numeric score change is
not a pass/fail flip; disjoint scorers are not comparable; a reused id with a
changed input is not the same sample; scorers moving in opposite directions on
one sample is neither a regression nor a recovery. No re-running of evaluations,
no model calls, no web UI, no LLM judging of differences. See
[docs/assurance-boundary.md](docs/assurance-boundary.md).

[0.2.0]: https://github.com/repowazdogz-droid/inspect-replay/releases/tag/v0.2.0
[0.1.0]: https://github.com/repowazdogz-droid/inspect-replay/releases/tag/v0.1.0
