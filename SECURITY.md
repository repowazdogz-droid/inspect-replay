# Security policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x | yes |

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

## What this means for your data

Evaluation logs frequently contain prompts, model completions, and dataset
content that you may not want to disclose. inspect-replay does not transmit
them anywhere. It does, however, **quote them in its output**: error messages
and score values appear in the report, and the JSON output includes score values
and error strings.

If you paste a report into a public issue, read it first.

## Parsing untrusted logs

Logs are parsed with `inspect_ai.log.read_eval_log`, so inspect-replay's parsing
exposure is Inspect's parsing exposure. A malformed or hostile file is surfaced
as a `LoadError` rather than a stack trace, but the parsing itself happens
upstream. If you are comparing `.eval` files from a source you do not trust,
that trust decision is really about `inspect_ai` and the `zipfile`/`json`
libraries beneath it, not about this tool.
