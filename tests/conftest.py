"""Shared fixtures.

``make_log`` builds real ``EvalLog`` objects so that tests exercise the actual
Inspect schema rather than a mock of it. Mutation-style tests then change
exactly one field and assert that exactly one detector fires.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
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
    EvalSampleReductions,
    EvalSampleScore,
    EvalScore,
    EvalSpec,
    EvalStats,
)
from inspect_ai.model import ChatMessageUser, GenerateConfig, ModelOutput
from inspect_ai.scorer import Score

EXAMPLES = Path(__file__).parent.parent / "examples"

CREATED = "2026-01-01T00:00:00+00:00"


def _scores(
    scores: dict[str, Any] | None,
    error: str | None,
    scorer: str,
    value: Any,
    completion: str,
) -> dict[str, Score] | None:
    if scores is not None:
        return {k: Score(value=v) for k, v in scores.items()}
    if error:
        return None
    return {scorer: Score(value=value, answer=completion)}


def make_sample(
    sample_id: str | int,
    *,
    input: str = "What is 2 + 2?",
    target: str = "4",
    completion: str = "4",
    value: Any = "C",
    scorer: str = "exact_match",
    error: str | None = None,
    epoch: int = 1,
    metadata: dict[str, Any] | None = None,
    scores: dict[str, Any] | None = None,
) -> EvalSample:
    return EvalSample(
        id=sample_id,
        epoch=epoch,
        input=input,
        target=target,
        messages=[],
        output=ModelOutput.from_content(model="mockllm/model", content=completion),
        scores=_scores(scores, error, scorer, value, completion),
        error=EvalError(message=error, traceback="", traceback_ansi="") if error else None,
        metadata=metadata or {},
    )


def make_chat_sample(
    sample_id: str | int,
    *,
    text: str = "What is 2 + 2?",
    target: str = "4",
    completion: str = "4",
    value: Any = "C",
    scorer: str = "exact_match",
    epoch: int = 1,
) -> EvalSample:
    """A sample whose input is a list of ChatMessages.

    Every ChatMessage gets a fresh random uuid on construction, which is exactly
    what broke content hashing. Two calls to this helper with the same text
    produce inputs that are equal in content and unequal in their message ids.
    """
    return EvalSample(
        id=sample_id,
        epoch=epoch,
        input=[ChatMessageUser(content=text)],
        target=target,
        messages=[],
        output=ModelOutput.from_content(model="mockllm/model", content=completion),
        scores={scorer: Score(value=value, answer=completion)},
        metadata={},
    )


def make_log(
    samples: list[EvalSample],
    *,
    model: str = "mockllm/model",
    temperature: float = 0.0,
    scorer: str = "exact_match",
    task: str = "qa_benchmark",
    task_version: int = 1,
    dataset_name: str = "testset",
    dataset_samples: int | None = None,
    system_message: str = "You are helpful.",
    prompt_template: str | None = None,
    solver: str = "generate",
    sandbox: Any = None,
    tools: list[str] | None = None,
    packages: dict[str, str] | None = None,
    commit: str = "a" * 40,
    status: str = "success",
    location: str = "test.eval",
    accuracy: float | None = None,
    reductions: list[EvalSampleReductions] | None = None,
    epochs: int = 1,
) -> EvalLog:
    steps = [EvalPlanStep(solver="system_message", params={"template": system_message})]
    if prompt_template is not None:
        steps.append(EvalPlanStep(solver="prompt_template", params={"template": prompt_template}))
    if tools is not None:
        steps.append(
            EvalPlanStep(
                solver="use_tools",
                params={"tools": [{"type": "tool", "name": t} for t in tools]},
            )
        )
    steps.append(EvalPlanStep(solver=solver, params={}))

    if accuracy is None:
        scored = [s for s in samples if s.scores]
        correct = sum(
            1
            for s in scored
            if any(v.value in ("C", 1, 1.0, True) for v in (s.scores or {}).values())
        )
        accuracy = round(correct / len(scored), 4) if scored else 0.0

    log = EvalLog(
        version=2,
        status=status,  # type: ignore[arg-type]
        eval=EvalSpec(
            eval_id="eval-1",
            run_id="run-1",
            created=CREATED,
            task=task,
            task_id="task-1",
            task_version=task_version,
            task_args={},
            solver=solver,
            solver_args={},
            dataset=EvalDataset(
                name=dataset_name,
                location=f"datasets/{dataset_name}.jsonl",
                samples=dataset_samples if dataset_samples is not None else len(samples),
                sample_ids=[s.id for s in samples],
                shuffled=False,
            ),
            model=model,
            model_generate_config=GenerateConfig(
                temperature=temperature, max_tokens=512, system_message=system_message
            ),
            model_args={},
            sandbox=sandbox,
            config=EvalConfig(epochs=epochs),
            revision=EvalRevision(type="git", origin="git@example.com:o/r.git", commit=commit),
            packages={"inspect_ai": "0.3.246", **(packages or {})},
            scorers=[{"name": scorer, "options": {}, "metrics": []}],  # type: ignore[list-item]
        ),
        plan=EvalPlan(name="plan", steps=steps, config=GenerateConfig(temperature=temperature)),
        results=EvalResults(
            total_samples=len(samples),
            completed_samples=len([s for s in samples if s.error is None]),
            scores=[
                EvalScore(
                    name=scorer,
                    scorer=scorer,
                    metrics={"accuracy": EvalMetric(name="accuracy", value=accuracy)},
                )
            ],
        ),
        stats=EvalStats(started_at=CREATED, completed_at=CREATED),
        samples=samples,
        location=location,
    )
    # Assigned after construction: EvalLog's resolve_sample_reductions validator
    # tries to write into results as a dict, which fails when results is passed
    # as a constructed model.
    if reductions is not None:
        log.reductions = reductions
    return log


def make_tool_log(samples: list[EvalSample], *, tools: list[str]) -> EvalLog:
    """A log whose plan records a tool set, as use_tools() does."""
    return make_log(samples, tools=tools)


def make_reduced_log(
    *,
    epoch_values: list[Any],
    reduced: Any,
    sample_id: str = "q1",
    scorer: str = "exact_match",
) -> EvalLog:
    """A multi-epoch log carrying both raw epoch scores and a reduced score.

    ``epoch_values`` are the per-epoch scores; ``reduced`` is what the reducer
    produced, and is the value the evaluation's metric is computed from.
    """
    samples = [
        make_sample(sample_id, epoch=i + 1, scores={scorer: v}) for i, v in enumerate(epoch_values)
    ]
    reductions = [
        EvalSampleReductions(
            scorer=scorer,
            reducer="max",
            samples=[EvalSampleScore(value=reduced, sample_id=sample_id)],
        )
    ]
    return make_log(samples, scorer=scorer, reductions=reductions, epochs=len(epoch_values))


@pytest.fixture
def base_samples() -> list[EvalSample]:
    return [
        make_sample("q1", input="What is 2 + 2?", target="4", completion="4", value="C"),
        make_sample(
            "q2", input="Capital of France?", target="Paris", completion="Paris", value="C"
        ),
        make_sample("q3", input="15 * 3?", target="45", completion="45", value="C"),
        make_sample(
            "q4", input="Who wrote Hamlet?", target="Shakespeare", completion="?", value="I"
        ),
    ]


@pytest.fixture
def baseline(base_samples: list[EvalSample]) -> EvalLog:
    return make_log(base_samples, location="baseline.eval")
