# Security policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.2.x | yes |
| 0.1.x | no |

**0.1.0 is superseded and should not be used.** It contains known security and
correctness defects that 0.2.0 fixes: a terminal-control-injection issue (a
crafted `.eval` could inject ANSI escapes into the text report and forge a
verdict), a dependency floor that did not work (`inspect_ai>=0.3.100`), an exit
code that reported a comparison as "unchanged" when no sample could be aligned,
and a sample-alignment bug that mislabelled chat-format regressions. See the
[CHANGELOG](CHANGELOG.md) for the full list. The `v0.1.0` git tag is left intact
for history; it is not a supported release.

## Reporting a vulnerability

Report privately via
[GitHub Security Advisories](https://github.com/repowazdogz-droid/inspect-replay/security/advisories/new).
Please do not open a public issue for a vulnerability.

Expect an acknowledgement within 7 days.

## Threat model

inspect-replay is a local, read-only, offline command line tool.

- It **reads** two files and writes a report to stdout or to a path you name.
- It never writes to the logs it reads. `test_comparison_does_not_modify_the_input_logs`
  asserts this by hashing them before and after.
- It makes **no network calls**. It does not contact a model provider, a
  telemetry endpoint, or anything else.
- It executes no code from the logs and runs no sandbox.
- Its only runtime dependency is `inspect_ai`.

Two caveats to "safe on untrusted input", both detailed below: the text report
must neutralise terminal-control sequences in quoted log content (it does), and
parsing a decompression-bomb `.eval` can exhaust memory (it cannot yet bound
this — run under a memory limit for untrusted files).

## What this means for your data

Evaluation logs frequently contain prompts, model completions, and dataset
content that you may not want to disclose. inspect-replay does not transmit
them anywhere. It does, however, **quote them in its output**: error messages
and score values appear in the report, and the JSON output includes score values
and error strings.

If you paste a report into a public issue, read it first.

## Terminal-control injection (mitigated)

An `.eval` log is untrusted input, and the text report prints values taken from
it -- error messages, score values, model names, and the log location. Those are
attacker-controllable. Without mitigation, a crafted log could embed ANSI/OSC
escape sequences that clear the screen, move the cursor, set the terminal title,
or print a **forged green `Evaluation: UNCHANGED` line** -- especially damaging
for a tool whose entire output is a trust verdict.

inspect-replay **strips control characters from untrusted content before
printing** to the text report — both values and the identifiers that carry
untrusted substrings (error messages, score values, model names,
dataset/scorer/package names, sample ids, the log location)
(`inspect_replay.text.sanitize`). Regression tests feed raw `0x1b`/`0x07`, the
C1 CSI `0x9b`, backspace, and carriage return through each of these fields and
assert no control character reaches stdout. The JSON output is protected by `ensure_ascii=True`, which escapes every non-ASCII byte -- including DEL and the C1 range that `json.dumps` would otherwise emit raw -- to `\uXXXX`. A test feeds ESC, DEL, and the C1 CSI through the JSON path and asserts no raw control byte survives.

The authored-prose parts of the report are additionally kept free of quoted log
text (see the causal-language note in `docs/assurance-boundary.md`), so a log
cannot inject wording into a sentence the tool is asserting.

## Resource exhaustion when parsing untrusted logs

An `.eval` is a zip archive, and zip archives can be **decompression bombs**. A
small crafted file (~800 KB in testing) whose members expand to hundreds of
megabytes drove the loader to multiple gigabytes of resident memory in a couple
of seconds before failing validation; a larger one can OOM-kill the process.

This blowup happens inside stdlib `zipfile`/`json` and `inspect_ai`'s reader,
which decompress before inspect-replay sees the data, so inspect-replay cannot
currently bound it. **If you compare `.eval` files from an untrusted source, run
the tool under a memory limit** (e.g. `ulimit -v`, a container limit, or a cgroup)
until an upstream size guard exists. A pre-parse size check is tracked as future
work.

## Parsing untrusted logs

Logs are parsed with `inspect_ai.log.read_eval_log`, so inspect-replay's parsing
exposure is Inspect's parsing exposure. A malformed or hostile file is surfaced
as a `LoadError` rather than a stack trace, but the parsing itself happens
upstream. If you are comparing `.eval` files from a source you do not trust,
that trust decision is really about `inspect_ai` and the `zipfile`/`json`
libraries beneath it, not about this tool -- with the two exceptions above
(terminal injection, which this tool mitigates, and decompression bombs, which
it cannot yet bound).
