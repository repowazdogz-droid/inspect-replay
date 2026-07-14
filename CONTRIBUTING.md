# Contributing

## Setup

```console
git clone https://github.com/repowazdogz-droid/inspect-replay
cd inspect-replay
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Before opening a PR

```console
ruff check .
ruff format --check .
mypy
pytest
```

CI runs exactly these on Python 3.11, 3.12, and 3.13, plus a check that the
example logs are reproducible from the committed generator and that a built
wheel installs and runs in a clean environment.

## The rules that are not negotiable

This tool's value is that its output can be trusted. Four properties protect
that, and each is enforced by a test rather than by convention.

**1. Never encode unknown information as false or as "unchanged."**

If the log does not record a field, the status is `UNKNOWN`. It is never `SAME`.
Guarded by `test_unknown_is_never_encoded_as_false_in_json` and
`test_recorded_empty_args_are_same_not_unknown` — note that rule cuts both ways:
a recorded `{}` is `SAME`, not `UNKNOWN`.

**2. Never report "unchanged" for a comparison that did not happen.**

If no sample could be aligned, the verdict is `NOT_COMPARABLE` and the exit code
is 2. Never `UNCHANGED`, never 0. The exit code is what a CI gate acts on, and a
tool that returns "no differences" for an evaluation whose samples it never read
will pass the gate on a run that collapsed. Guarded by
`test_not_comparable_exits_with_an_error_code`.

**3. Never claim causation.**

inspect-replay reads two snapshots. It cannot hold a variable fixed and cannot
re-run anything, so it cannot establish that any change produced any result.
Report co-occurrence: "changed alongside", "possible contributor", "cannot be
determined from the recorded data". Guarded by
`test_no_causal_wording_in_any_generated_report`, which fails the build if
causal vocabulary appears in any generated output.

**4. Never align samples by position silently.**

If two samples are matched that are not the same sample, every regression
reported afterwards is fiction that reads as fact. Positional alignment stays
behind an explicit flag, and everything it produces stays labelled weak. Guarded
by `test_positional_alignment_is_never_used_silently`.

If a change would require weakening one of these, that is a discussion to have
in an issue first, not a diff to send.

`tests/test_regressions.py` is the graveyard of bugs that violated these rules in
earlier versions of this tool — a chat input whose random message uuids made every
regression look like a changed question, an `UNCHANGED` verdict on a log with no
samples, a `tools: NOT_CHECKED` that told users a recorded field was unrecordable.
Read it before changing `align.py` or `sample_diff.py`.

## Adding a detector

Every detector needs a mutation-style test pair: one showing it **fires** on its
intended change, one showing it stays **silent** on unrelated changes. A
detector that reports a model change when only the temperature moved sends an
engineer to investigate the wrong thing. See `tests/test_detectors.py`.

Every conclusion must carry `evidence` — the dotted `EvalLog` field paths it was
derived from. `test_every_config_conclusion_carries_evidence` enforces this.

## Changing the JSON schema

The JSON output is a public contract for automation. Any change to its shape
bumps `SCHEMA_VERSION` in `models.py` and is noted in `CHANGELOG.md`.
`test_json_schema_shape_is_stable` pins the current shape and will fail, which
is the intended behaviour — update it deliberately.

## Scope

v0.1 is deliberately finished. Two logs in, one report out. Comparison of more
than two logs, statistical significance testing, re-running evaluations, model
calls, and a web UI are all out of scope. If you want statistically-tested
regression analysis of Inspect runs,
[inspect-mlflow](https://github.com/debu-sinha/inspect-mlflow) already does it.

Bug reports and correctness fixes are very welcome. So is any case where the
tool told you something misleading — that is the most valuable issue you can
file.
