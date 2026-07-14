"""Comparison of the recorded configuration and headline results of two runs.

Every ``FieldDiff`` produced here names the ``EvalLog`` fields it was read from,
so that any conclusion in the report can be traced back to the recorded data
that produced it.

Two distinctions this module is careful about:

* **Recorded-and-empty is not unrecorded.** A ``task_args`` of ``{}`` was
  recorded, and equals the other log's ``{}``. That is ``SAME``. Coercing empty
  containers to "missing" would report a field as UNKNOWN that the log states
  plainly.
* **Not-recorded is not unchanged.** Where the schema genuinely carries no
  value, the status is ``UNKNOWN``, and the report says so rather than implying
  the field was checked and found identical.
"""

from __future__ import annotations

from typing import Any

from inspect_ai.log import EvalLog

from .models import FieldDiff, Status

__all__ = ["compare_config", "compare_results", "generation_fields_changed"]

_MISSING = object()

# Generation parameters whose change can plausibly move model output. Used to
# rank observations; not an exhaustive list of what is compared.
NONDETERMINISM_FIELDS = frozenset(
    {"temperature", "top_p", "top_k", "seed", "num_choices", "best_of", "logprobs"}
)

# Plan-step parameter names under which Inspect's solvers record prompt text.
# Both system_message() and prompt_template() record theirs as "template", so
# the solver name has to be carried alongside the value: reporting a
# prompt_template change under the label "system message" would name a field
# that was not read.
_PROMPT_PARAMS = ("template", "system_message", "message", "prompt")


def _plain(value: Any) -> Any:
    """Render a value for reporting: pydantic models become dicts."""
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _present(value: Any) -> Any:
    """Pass a value through, mapping only genuine absence to _MISSING.

    ``None`` is absence. ``{}``, ``[]``, ``0``, and ``False`` are recorded
    values and are compared as such.
    """
    return _MISSING if value is None else value


def _diff(
    name: str,
    old: Any,
    new: Any,
    evidence: tuple[str, ...],
    *,
    note: str | None = None,
) -> FieldDiff:
    """Compare one field, distinguishing absence from equality.

    A field absent from both logs is UNKNOWN, not SAME: we did not observe that
    it was the same, we observed nothing at all.
    """
    old_absent = old is _MISSING or old is None
    new_absent = new is _MISSING or new is None
    old_p = None if old_absent else _plain(old)
    new_p = None if new_absent else _plain(new)

    if old_absent and new_absent:
        return FieldDiff(
            field=name,
            status=Status.UNKNOWN,
            evidence=evidence,
            note=note or "not recorded in either log",
        )
    if old_absent:
        return FieldDiff(name, Status.ADDED, None, new_p, evidence, note)
    if new_absent:
        return FieldDiff(name, Status.REMOVED, old_p, None, evidence, note)
    if old_p == new_p:
        return FieldDiff(name, Status.SAME, old_p, new_p, evidence, note)
    return FieldDiff(name, Status.CHANGED, old_p, new_p, evidence, note)


def _scorer_summary(log: EvalLog) -> Any:
    scorers = log.eval.scorers
    if scorers is None:
        return _MISSING
    return [{"name": s.name, "options": _plain(s.options) or {}} for s in scorers]


def _solver_steps(log: EvalLog) -> Any:
    steps = log.plan.steps if log.plan else None
    if steps is None:
        return _MISSING
    return [{"solver": s.solver, "params": _plain(s.params) or {}} for s in steps]


def _tools(log: EvalLog) -> Any:
    """Recover the configured tool set from where Inspect actually records it.

    ``EvalSpec`` has no ``tools`` field, but that does NOT mean the tool set is
    unrecorded: ``use_tools()`` writes it into ``plan.steps[].params["tools"]``,
    and the plan is part of the log header. An earlier version of this tool
    reported ``tools: NOT_CHECKED``, explaining that Inspect does not record
    them. That was wrong, and wrong in the worst direction: it told users that a
    change they would care about (a model being handed a shell) was undetectable,
    while the data sat in the log.

    Returns ``_MISSING`` only when no plan step records tools.
    """
    tools: list[Any] = []
    for step in log.plan.steps if log.plan else []:
        params = step.params or {}
        if "tools" in params:
            tools.append({"solver": step.solver, "tools": _plain(params["tools"])})
    return tools if tools else _MISSING


def _prompts(log: EvalLog) -> Any:
    """Recover prompt text from every plan step that records it.

    ``system_message()`` and ``prompt_template()`` both record their text under
    the parameter name ``template``, so a lookup that takes the first match and
    calls it "the system message" will silently label a prompt-template change
    as a system-message change, and will miss the second of the two entirely.
    Every prompt-bearing step is collected, tagged with the solver that recorded
    it, and compared as a whole.
    """
    prompts: list[dict[str, Any]] = []

    gc = getattr(log.eval, "model_generate_config", None)
    direct = getattr(gc, "system_message", None) if gc else None
    if direct:
        prompts.append({"source": "generate_config.system_message", "text": direct})

    for index, step in enumerate(log.plan.steps if log.plan else []):
        params = step.params or {}
        for key in _PROMPT_PARAMS:
            if key in params and isinstance(params[key], str):
                prompts.append(
                    {"source": f"plan.steps[{index}].{step.solver}.{key}", "text": params[key]}
                )
                break

    return prompts if prompts else _MISSING


def _sample_ids_diff(old_ids: Any, new_ids: Any) -> FieldDiff:
    """Compare the dataset's sample ids as a SET, reporting order separately.

    An ordered comparison reports "the dataset changed" when a run was merely
    shuffled, which then leads the report with the claim that the two runs did
    not evaluate the same sample set. They did.
    """
    evidence = ("eval.dataset.sample_ids",)
    if not old_ids and not new_ids:
        return FieldDiff(
            "dataset.sample_ids",
            Status.UNKNOWN,
            evidence=evidence,
            note="not recorded in either log",
        )
    old_set = {str(i) for i in (old_ids or [])}
    new_set = {str(i) for i in (new_ids or [])}
    if old_set != new_set:
        return FieldDiff(
            "dataset.sample_ids",
            Status.CHANGED,
            sorted(old_set),
            sorted(new_set),
            evidence,
            note=(
                f"{len(old_set - new_set)} id(s) only in the old log, "
                f"{len(new_set - old_set)} only in the new"
            ),
        )
    if list(old_ids or []) != list(new_ids or []):
        return FieldDiff(
            "dataset.sample_ids",
            Status.SAME,
            sorted(old_set),
            sorted(new_set),
            evidence,
            note=(
                "the same sample ids in a different order (the dataset was shuffled "
                "differently). The sample set is identical, so results remain comparable."
            ),
        )
    return FieldDiff("dataset.sample_ids", Status.SAME, sorted(old_set), sorted(new_set), evidence)


def _scorer_diff(old: EvalLog, new: EvalLog) -> FieldDiff:
    o_s, n_s = _scorer_summary(old), _scorer_summary(new)
    d = _diff("scorer", o_s, n_s, ("eval.scorers[].name", "eval.scorers[].options"))
    if d.status is not Status.CHANGED:
        return d
    old_names = [s["name"] for s in (o_s if isinstance(o_s, list) else [])]
    new_names = [s["name"] for s in (n_s if isinstance(n_s, list) else [])]
    note = (
        "scorer names changed"
        if old_names != new_names
        else "same scorer names, but scorer options changed"
    )
    return FieldDiff(d.field, d.status, d.old, d.new, d.evidence, note)


def compare_config(old: EvalLog, new: EvalLog) -> tuple[FieldDiff, ...]:
    """Compare every configuration field inspect-replay reads.

    Returns a deterministic, ordered tuple: the same input logs give the same
    output every time.
    """
    diffs: list[FieldDiff] = []
    o, n = old.eval, new.eval

    # --- task identity ---
    diffs.append(_diff("task.name", o.task, n.task, ("eval.task",)))
    diffs.append(_diff("task.version", o.task_version, n.task_version, ("eval.task_version",)))
    diffs.append(_diff("task.file", o.task_file, n.task_file, ("eval.task_file",)))
    diffs.append(
        _diff("task.args", _present(o.task_args), _present(n.task_args), ("eval.task_args",))
    )

    # --- model ---
    diffs.append(_diff("model.name", o.model, n.model, ("eval.model",)))
    diffs.append(
        _diff("model.base_url", o.model_base_url, n.model_base_url, ("eval.model_base_url",))
    )
    diffs.append(
        _diff("model.args", _present(o.model_args), _present(n.model_args), ("eval.model_args",))
    )
    diffs.append(
        _diff(
            "model.roles", _present(o.model_roles), _present(n.model_roles), ("eval.model_roles",)
        )
    )

    # --- generation config, field by field so a temperature change is named ---
    ogc = _plain(o.model_generate_config) or {}
    ngc = _plain(n.model_generate_config) or {}
    for key in sorted(set(ogc) | set(ngc)):
        diffs.append(
            _diff(
                f"generate_config.{key}",
                ogc.get(key, _MISSING),
                ngc.get(key, _MISSING),
                (f"eval.model_generate_config.{key}",),
            )
        )

    # --- prompts ---
    diffs.append(
        _diff(
            "prompt",
            _prompts(old),
            _prompts(new),
            ("eval.model_generate_config.system_message", "plan.steps[].params"),
            note=(
                "collected from the generation config and from every plan step that records "
                "prompt text, each tagged with the solver that recorded it. Inspect has no "
                "single canonical prompt field, so UNKNOWN means no prompt text was recorded "
                "at the configuration level, not that none was used."
            ),
        )
    )

    # --- dataset ---
    diffs.append(_diff("dataset.name", o.dataset.name, n.dataset.name, ("eval.dataset.name",)))
    diffs.append(
        _diff(
            "dataset.location", o.dataset.location, n.dataset.location, ("eval.dataset.location",)
        )
    )
    diffs.append(
        _diff("dataset.samples", o.dataset.samples, n.dataset.samples, ("eval.dataset.samples",))
    )
    diffs.append(
        _diff(
            "dataset.shuffled", o.dataset.shuffled, n.dataset.shuffled, ("eval.dataset.shuffled",)
        )
    )
    diffs.append(_sample_ids_diff(o.dataset.sample_ids, n.dataset.sample_ids))

    # --- scorer ---
    diffs.append(_scorer_diff(old, new))

    # --- solver ---
    diffs.append(_diff("solver.name", o.solver, n.solver, ("eval.solver",)))
    diffs.append(
        _diff(
            "solver.args", _present(o.solver_args), _present(n.solver_args), ("eval.solver_args",)
        )
    )
    diffs.append(_diff("solver.steps", _solver_steps(old), _solver_steps(new), ("plan.steps",)))
    diffs.append(
        _diff(
            "plan.name",
            old.plan.name if old.plan else _MISSING,
            new.plan.name if new.plan else _MISSING,
            ("plan.name",),
        )
    )

    # --- sandbox and tools ---
    diffs.append(_diff("sandbox", o.sandbox, n.sandbox, ("eval.sandbox",)))
    diffs.append(
        _diff(
            "tools",
            _tools(old),
            _tools(new),
            ("plan.steps[].params.tools",),
            note=(
                "read from plan steps that record a tool set, as use_tools() does. Inspect "
                "has no tools field on the eval spec, so a solver that configures tools "
                "without recording them in its plan params is not visible here: UNKNOWN "
                "means no plan step recorded tools, not that no tools were used."
            ),
        )
    )

    # --- run config (epochs, limits) ---
    ocfg = _plain(o.config) or {}
    ncfg = _plain(n.config) or {}
    for key in sorted(set(ocfg) | set(ncfg)):
        diffs.append(
            _diff(
                f"config.{key}",
                ocfg.get(key, _MISSING),
                ncfg.get(key, _MISSING),
                (f"eval.config.{key}",),
            )
        )

    # --- environment provenance ---
    diffs.append(
        _diff(
            "revision.commit",
            o.revision.commit if o.revision else _MISSING,
            n.revision.commit if n.revision else _MISSING,
            ("eval.revision.commit",),
        )
    )
    diffs.append(
        _diff(
            "revision.dirty",
            o.revision.dirty if o.revision else _MISSING,
            n.revision.dirty if n.revision else _MISSING,
            ("eval.revision.dirty",),
            note=(
                "a dirty working tree means the recorded commit does not fully describe "
                "the code that ran"
            ),
        )
    )
    for pkg in sorted(set(o.packages or {}) | set(n.packages or {})):
        diffs.append(
            _diff(
                f"packages.{pkg}",
                (o.packages or {}).get(pkg, _MISSING),
                (n.packages or {}).get(pkg, _MISSING),
                (f"eval.packages.{pkg}",),
            )
        )

    return tuple(diffs)


def compare_results(old: EvalLog, new: EvalLog) -> tuple[FieldDiff, ...]:
    """Compare the headline metrics the evaluation itself reports.

    ``EvalResults.scores[].metrics`` holds the numbers a user quotes: accuracy,
    stderr, and whatever else the task defines. They live in the log HEADER, so
    they survive ``log_samples=False`` -- which is exactly the case where a
    sample-level comparison can say nothing at all. A tool that reads only
    samples reports "unchanged" on a run whose accuracy fell by seventy points.
    """
    diffs: list[FieldDiff] = []

    def metrics(log: EvalLog) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if log.results is None:
            return out
        for score in log.results.scores:
            for metric_name, metric in (score.metrics or {}).items():
                out[f"{score.name}.{metric_name}"] = metric.value
        return out

    old_m, new_m = metrics(old), metrics(new)
    for key in sorted(set(old_m) | set(new_m)):
        diffs.append(
            _diff(
                f"results.{key}",
                old_m.get(key, _MISSING),
                new_m.get(key, _MISSING),
                (f"results.scores[].metrics.{key}",),
            )
        )

    diffs.append(
        _diff(
            "results.total_samples",
            old.results.total_samples if old.results else _MISSING,
            new.results.total_samples if new.results else _MISSING,
            ("results.total_samples",),
        )
    )
    diffs.append(
        _diff(
            "results.completed_samples",
            old.results.completed_samples if old.results else _MISSING,
            new.results.completed_samples if new.results else _MISSING,
            ("results.completed_samples",),
        )
    )
    return tuple(diffs)


def generation_fields_changed(diffs: tuple[FieldDiff, ...]) -> list[FieldDiff]:
    """Changed generation parameters that could move model output."""
    return [
        d
        for d in diffs
        if d.status is Status.CHANGED
        and d.field.startswith("generate_config.")
        and d.field.split(".", 1)[1] in NONDETERMINISM_FIELDS
    ]
