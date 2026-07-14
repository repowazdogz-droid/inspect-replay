# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-07-14

First release.

### Added

- Requires `inspect_ai>=0.3.180` — the oldest release the tool is actually
  tested against (below it the reader cannot open the example logs). A CI job
  installs this exact floor and runs the suite, so it cannot silently drift.
- `inspect-replay compare OLD NEW` — compares two Inspect `.eval` logs.
- Configuration diff across task, model, model args, every generation parameter
  individually, prompt text from every plan step that records it, dataset
  identity and size, scorer names and options, solver and plan steps, sandbox,
  the configured tool set (from `plan.steps[].params["tools"]`), run config,
  installed package versions, and git revision.
- Comparison of the headline metrics the evaluation reports (`EvalResults`).
  These live in the log header, so they are compared even when the logs record
  no samples.
- Sample alignment on the strongest stable key available: `(id, epoch)`, then a
  content hash of input, target, and choices. The hash strips volatile
  identifiers, so a chat-format input hashes by content rather than by the random
  uuid Inspect assigns each `ChatMessage`. Positional alignment exists only
  behind `--allow-positional-alignment` and is labelled weak.
- Where both logs record `reductions` (epochs > 1), the epoch-reduced score is
  compared — that is the score the evaluation's metric is computed from. Where
  they do not, raw epoch rows are compared and the unit is labelled
  `sample-epoch row`.
- Sample classification: unchanged, newly passing, newly failing, score changed,
  mixed, error introduced, error resolved, errored in both, added, removed,
  input changed, scores not comparable, unknown.
- Ranked observations naming the log fields each was derived from, worded as
  co-occurrence rather than causation.
- Human-readable text report and versioned JSON output (`schema_version` 0.1, pre-1.0).
- Exit codes for CI: 0 no differences, 1 differences found, 2 a log could not
  be read, 3 the logs were read but no sample could be aligned.
- Four example `.eval` logs written with Inspect's own writer, plus a malformed
  file, and a committed generator that reproduces them.

Exit codes: `0` no differences, `1` differences found, `2` a log could not be
read, `3` the logs were read but no sample could be aligned. The two failure
modes are kept distinct so a CI gate can tell "your file is broken" from "your
runs don't line up".

### Security and robustness

- The text report strips terminal-control characters from every untrusted string
  before printing — values AND identifiers (error messages, score values, model
  names, dataset/scorer/package names, sample ids, the log location) — so a
  crafted `.eval` cannot inject ANSI escapes to clear the screen or forge a green
  verdict. The JSON path was already safe (`json.dumps` escapes them).
- The causal-language build check now matches causal verbs and constructions
  (`caused`, `led to`, `triggered`, `drove`, …) rather than a substring
  blocklist, and runs over the tool's authored prose only. The tool no longer
  splices untrusted log content (error strings, model names, metric and
  generation values, sample ids) into its own sentences; those are shown in their
  sections instead. `docs/assurance-boundary.md` states plainly what the check
  does and does not cover.
- Decompression-bomb exposure during parsing (upstream in `inspect_ai`/`zipfile`)
  is documented in `SECURITY.md` with a mitigation (run under a memory limit for
  untrusted logs).

### Deliberate non-features

Unrecorded information is `UNKNOWN`, never "unchanged". A comparison that could
not be performed — no samples logged, nothing aligned — is `NOT_COMPARABLE` with
exit code 3, never `UNCHANGED` with exit 0: a tool that reports "no differences"
for an evaluation it never read would pass a CI gate on a run that collapsed.

A numeric score change is not a pass/fail flip; disjoint scorers are not
comparable; a reused id with a changed input is not the same sample; scorers
moving in opposite directions on one sample is neither a regression nor a
recovery.

The JSON `schema_version` is `0.1`: pre-1.0 and not yet frozen. The machine
contract (enum values, numeric counters, evidence fields) is stable within a
version; prose fields (`observations[].statement`, notes) and the opaque
`sample[].key` are not — see `json_output.py`.

No re-running of evaluations, no model calls, no web UI, no LLM judging of
differences. See [docs/assurance-boundary.md](docs/assurance-boundary.md).

[0.1.0]: https://github.com/repowazdogz-droid/inspect-replay/releases/tag/v0.1.0
