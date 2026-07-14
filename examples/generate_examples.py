"""Generate the bundled example logs using Inspect's own writer.

The examples are written with ``inspect_ai.log.write_eval_log`` against real
``EvalLog`` objects, so they are genuine ``.eval`` files with the real schema,
not hand-forged JSON that happens to parse.

They are synthetic: no model was called to produce them. Model outputs, scores,
and errors are constructed to exercise one specific difference each, so that a
test can show a detector firing on its intended change and staying silent on
everything else.

Regenerate with:

    python examples/generate_examples.py

The output is deterministic: no timestamps, uuids, or random values are drawn
at generation time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from inspect_ai.log import (
    EvalConfig,
    EvalDataset,
    EvalError,
    EvalLog,
    EvalMetric,
    EvalPlan,
    EvalPlanStep,
    EvalResults,
    EvalRevision,
    EvalSample,
    EvalScore,
    EvalSpec,
    EvalStats,
    write_eval_log,
)
from inspect_ai.model import GenerateConfig, ModelOutput
from inspect_ai.scorer import Score

# EvalScorer is not exported from inspect_ai.log, so scorers are supplied as
# dicts and validated into EvalScorer by pydantic. This keeps the generator off
# Inspect's private modules.

HERE = Path(__file__).parent

CREATED = "2026-01-01T00:00:00+00:00"
QUESTIONS = [
    ("q1", "What is 2 + 2?", "4", "4"),
    ("q2", "What is the capital of France?", "Paris", "Paris"),
    ("q3", "What is 15 * 3?", "45", "45"),
    ("q4", "Who wrote Hamlet?", "Shakespeare", "Shakespeare"),
    ("q5", "What is the boiling point of water in Celsius?", "100", "100"),
    ("q6", "What is the square root of 144?", "12", "12"),
    ("q7", "In what year did the Berlin Wall fall?", "1989", "1989"),
    ("q8", "What is the chemical symbol for gold?", "Au", "Au"),
]


def _sample(
    sample_id: str,
    question: str,
    target: str,
    answer: str,
    value: Any,
    *,
    error: str | None = None,
    scorer: str = "exact_match",
) -> EvalSample:
    """One sample. ``value`` is the recorded score value ("C"/"I", or numeric)."""
    scores = (
        None
        if error
        else {scorer: Score(value=value, answer=answer, explanation=f"answered {answer!r}")}
    )
    return EvalSample(
        id=sample_id,
        epoch=1,
        input=question,
        target=target,
        messages=[],
        output=ModelOutput.from_content(model="mockllm/model", content=answer),
        scores=scores,
        error=EvalError(message=error, traceback="", traceback_ansi="") if error else None,
        metadata={},
    )


def _log(
    *,
    model: str = "mockllm/model",
    temperature: float = 0.0,
    scorer: str = "exact_match",
    samples: list[EvalSample],
    dataset_size: int | None = None,
    dataset_name: str = "arithmetic_and_facts",
    system_message: str = "You are a helpful assistant. Answer with the answer only.",
    task_version: int = 1,
    packages: dict[str, str] | None = None,
    commit: str = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
) -> EvalLog:
    accuracy = sum(
        1
        for s in samples
        if s.scores and any(v.value in ("C", 1, 1.0, True) for v in s.scores.values())
    ) / max(len(samples), 1)

    return EvalLog(
        version=2,
        status="success",
        eval=EvalSpec(
            eval_id="eval-fixed-0001",
            run_id="run-fixed-0001",
            created=CREATED,
            task="qa_benchmark",
            task_id="task-fixed-0001",
            task_version=task_version,
            task_file="tasks/qa_benchmark.py",
            task_args={},
            solver="generate",
            solver_args={},
            dataset=EvalDataset(
                name=dataset_name,
                location=f"datasets/{dataset_name}.jsonl",
                samples=dataset_size if dataset_size is not None else len(samples),
                sample_ids=[s.id for s in samples],
                shuffled=False,
            ),
            model=model,
            model_generate_config=GenerateConfig(
                temperature=temperature,
                max_tokens=512,
                system_message=system_message,
            ),
            model_args={},
            config=EvalConfig(epochs=1, limit=len(samples)),
            revision=EvalRevision(
                type="git", origin="git@example.com:org/evals.git", commit=commit
            ),
            packages={"inspect_ai": "0.3.246", **(packages or {})},
            scorers=[{"name": scorer, "options": {}, "metrics": []}],  # type: ignore[list-item]
        ),
        plan=EvalPlan(
            name="qa_plan",
            steps=[
                EvalPlanStep(solver="system_message", params={"template": system_message}),
                EvalPlanStep(solver="generate", params={}),
            ],
            config=GenerateConfig(temperature=temperature),
        ),
        results=EvalResults(
            total_samples=len(samples),
            completed_samples=sum(1 for s in samples if s.error is None),
            scores=[
                EvalScore(
                    name=scorer,
                    scorer=scorer,
                    metrics={"accuracy": EvalMetric(name="accuracy", value=round(accuracy, 4))},
                )
            ],
        ),
        stats=EvalStats(started_at=CREATED, completed_at=CREATED),
        samples=samples,
    )


def _baseline_samples() -> list[EvalSample]:
    """All 8 correct except q7 and q8, which are wrong."""
    out = []
    for sid, question, target, answer in QUESTIONS:
        wrong = sid in ("q7", "q8")
        out.append(
            _sample(
                sid,
                question,
                target,
                answer if not wrong else "unsure",
                "I" if wrong else "C",
            )
        )
    return out


def build() -> dict[str, EvalLog]:
    logs: dict[str, EvalLog] = {}

    logs["baseline"] = _log(samples=_baseline_samples())

    # Only the model name and the completions differ. Config diff must report
    # model.name and nothing else; sample diff must show two flips.
    model_samples = []
    for sid, question, target, answer in QUESTIONS:
        if sid == "q7":  # recovered
            model_samples.append(_sample(sid, question, target, answer, "C"))
        elif sid == "q3":  # regressed
            model_samples.append(_sample(sid, question, target, "46", "I"))
        else:
            wrong = sid == "q8"
            model_samples.append(
                _sample(
                    sid, question, target, answer if not wrong else "unsure", "I" if wrong else "C"
                )
            )
    logs["model-change"] = _log(model="mockllm/model-v2", samples=model_samples)

    # Only the scorer differs. Same model, same completions -- but the scorer
    # now emits numeric partial credit, which has NO binary pass/fail reading.
    # This is the case where a naive tool would invent regressions.
    scorer_samples = []
    for sid, question, target, answer in QUESTIONS:
        wrong = sid in ("q7", "q8")
        scorer_samples.append(
            _sample(
                sid,
                question,
                target,
                answer if not wrong else "unsure",
                0.25 if wrong else 0.9,
                scorer="model_graded_qa",
            )
        )
    logs["scorer-change"] = _log(scorer="model_graded_qa", samples=scorer_samples)

    # Same config throughout. Only sample outcomes move: 2 regressions, 1
    # recovery, 1 new error. This is the "nothing in the config explains it"
    # case, which must produce the nondeterminism observation.
    regression_samples = []
    for sid, question, target, answer in QUESTIONS:
        if sid in ("q1", "q2"):  # regressed
            regression_samples.append(_sample(sid, question, target, "I dont know", "I"))
        elif sid == "q7":  # recovered
            regression_samples.append(_sample(sid, question, target, answer, "C"))
        elif sid == "q4":  # errored
            regression_samples.append(
                _sample(
                    sid,
                    question,
                    target,
                    "",
                    "I",
                    error="ToolError: bash sandbox exited with code 137 (out of memory)",
                )
            )
        else:
            wrong = sid == "q8"
            regression_samples.append(
                _sample(
                    sid, question, target, answer if not wrong else "unsure", "I" if wrong else "C"
                )
            )
    logs["sample-regression"] = _log(samples=regression_samples)

    return logs


def main() -> None:
    for name, log in build().items():
        path = HERE / f"{name}.eval"
        if path.exists():
            path.unlink()
        write_eval_log(log, str(path))
        print(f"wrote {path.name}")

    # A file that is not a valid Inspect log, for testing the error path.
    malformed = HERE / "malformed.eval"
    malformed.write_bytes(b"this is not a zip archive and not an inspect eval log\n")
    print(f"wrote {malformed.name}")


if __name__ == "__main__":
    main()
