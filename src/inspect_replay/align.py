"""Putting two sample sets into correspondence.

Alignment is the step where a comparison tool can most easily lie. If two
samples are matched that are not in fact the same question, every downstream
statement about regressions is wrong -- and looks perfectly plausible.

Three rules follow, and they are enforced here rather than documented and hoped
for:

1. Positional alignment is never used unless the caller explicitly opts in, and
   when used it is labelled ``POSITIONAL`` so that every conclusion drawn from
   it carries the weak-alignment warning.
2. A matched id whose recorded input differs is NOT treated as the same sample.
   It is reported as ``INPUT_CHANGED`` and excluded from the pass/fail counts.
3. The input fingerprint hashes CONTENT ONLY. Inspect's ``ChatMessage`` carries
   a per-construction random ``id``, so hashing a naive model dump of a chat
   input makes two runs of the identical dataset look like two different
   datasets -- turning every real regression into a spurious "the question
   changed". Volatile identifiers are stripped before hashing. This is the
   single nastiest failure mode in the module and it is tested directly.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from inspect_ai.log import EvalLog, EvalSample

from .models import AlignmentMethod

__all__ = ["Alignment", "align_samples", "input_fingerprint"]

VOLATILE_KEYS = frozenset({"id", "internal", "source"})
"""Fields that vary between two constructions of the same content.

``ChatMessage.id`` defaults to a fresh uuid on every construction, so it differs
between two runs of the same dataset. ``source`` records whether a message came
from a generation or the input, and ``internal`` holds provider-specific state.
None of them are part of the question being asked, and all of them would break
a content hash.
"""


def _canonical(value: Any, *, drop_volatile: bool = False) -> Any:
    """Reduce a value to a JSON-stable form.

    With ``drop_volatile``, identifier fields that vary between two
    constructions of the same content are removed, so that the result depends
    on what was asked and not on how the object happened to be built.
    """
    if hasattr(value, "model_dump"):
        return _canonical(value.model_dump(exclude_none=True), drop_volatile=drop_volatile)
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda kv: str(kv[0]))
        return {
            str(k): _canonical(v, drop_volatile=drop_volatile)
            for k, v in items
            if not (drop_volatile and str(k) in VOLATILE_KEYS)
        }
    if isinstance(value, (list, tuple)):
        return [_canonical(v, drop_volatile=drop_volatile) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def input_fingerprint(sample: EvalSample) -> str:
    """Deterministic content hash of the identifying fields of a sample.

    Hashes the input, the target, and the answer choices where present -- the
    things that define what was asked. Model output, scores, errors, and timings
    are excluded: they are what is being compared, so they must not participate
    in deciding which samples correspond.

    Volatile identifiers (message uuids and the like) are stripped, so two runs
    of the identical dataset produce identical fingerprints even when the input
    is a list of chat messages.
    """
    payload = {
        "input": _canonical(sample.input, drop_volatile=True),
        "target": _canonical(sample.target, drop_volatile=True),
        "choices": _canonical(sample.choices, drop_volatile=True),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Alignment:
    """The correspondence between two sample sets.

    ``pairs`` holds keys present on both sides. ``old_only`` and ``new_only``
    hold keys present on one side, which become REMOVED and ADDED samples --
    unless no alignment was possible at all, in which case they are unalignable
    and are reported as such rather than as additions and deletions.
    """

    method: AlignmentMethod
    note: str
    pairs: dict[str, tuple[EvalSample, EvalSample]]
    old_only: dict[str, EvalSample]
    new_only: dict[str, EvalSample]
    warnings: tuple[str, ...] = ()

    @property
    def performed(self) -> bool:
        """Whether a sample comparison actually happened."""
        return self.method is not AlignmentMethod.NONE


def _keys_by_id(log: EvalLog) -> dict[str, EvalSample] | None:
    """Key samples by (id, epoch). Returns None if that key is not usable."""
    keyed: dict[str, EvalSample] = {}
    for s in log.samples or []:
        # The schema requires an id, but a hand-edited or truncated log may not
        # carry one. Treat a missing or empty id as "no usable key" rather than
        # keying every such sample to the same string.
        if getattr(s, "id", None) in (None, ""):
            return None
        key = f"{s.id}::{s.epoch}"
        if key in keyed:
            return None  # not unique -- fall through to a weaker key
        keyed[key] = s
    return keyed


def _keys_by_hash(log: EvalLog) -> dict[str, EvalSample] | None:
    samples = log.samples or []
    counts = Counter(f"{input_fingerprint(s)}::{s.epoch}" for s in samples)
    if any(c > 1 for c in counts.values()):
        return None  # duplicate inputs -- a hash cannot disambiguate them
    return {f"{input_fingerprint(s)}::{s.epoch}": s for s in samples}


def align_samples(
    old: EvalLog,
    new: EvalLog,
    *,
    allow_positional: bool = False,
) -> Alignment:
    """Align two sample sets using the strongest stable key available.

    Key preference, strongest first:

    1. ``(sample id, epoch)``
    2. ``sha256(input, target, choices) + epoch`` -- when ids are missing,
       duplicated, or share no values across the two logs
    3. position -- only if ``allow_positional`` is set, and always labelled weak

    Args:
        allow_positional: opt in to the weak positional fallback. Off by
            default. When off and no stable key exists, the alignment method is
            ``NONE`` and no samples are paired.
    """
    old_samples = old.samples or []
    new_samples = new.samples or []

    if not old_samples or not new_samples:
        missing = [name for name, s in (("old", old_samples), ("new", new_samples)) if not s]
        return Alignment(
            method=AlignmentMethod.NONE,
            note=(
                f"no samples recorded in the {' and '.join(missing)} log; "
                "sample-level comparison is not possible"
            ),
            pairs={},
            # Samples on the populated side are unalignable, not removed/added.
            old_only={f"old[{i}]": s for i, s in enumerate(old_samples)},
            new_only={f"new[{i}]": s for i, s in enumerate(new_samples)},
            warnings=(
                f"the {' and '.join(missing)} log contains no samples (it may have been "
                "written with log_samples=False); no sample-level comparison was performed",
            ),
        )

    strategies: list[tuple[AlignmentMethod, str]] = [
        (AlignmentMethod.SAMPLE_ID, "recorded sample id and epoch"),
        (AlignmentMethod.INPUT_HASH, "content hash of sample input and target, with epoch"),
    ]

    for method, description in strategies:
        if method is AlignmentMethod.SAMPLE_ID:
            old_keyed, new_keyed = _keys_by_id(old), _keys_by_id(new)
        else:
            old_keyed, new_keyed = _keys_by_hash(old), _keys_by_hash(new)

        if old_keyed is None or new_keyed is None:
            continue
        if not set(old_keyed) & set(new_keyed):
            # A key that produces zero overlap has aligned nothing. Try the next
            # one rather than declaring every sample added and removed.
            continue

        return Alignment(
            method=method,
            note=f"aligned on {description}",
            pairs={k: (old_keyed[k], new_keyed[k]) for k in old_keyed.keys() & new_keyed.keys()},
            old_only={k: v for k, v in old_keyed.items() if k not in new_keyed},
            new_only={k: v for k, v in new_keyed.items() if k not in old_keyed},
        )

    if not allow_positional:
        return Alignment(
            method=AlignmentMethod.NONE,
            note=(
                "no stable key aligned these logs: sample ids are missing, not unique, or "
                "share no values, and input hashes do not match. Positional alignment was "
                "NOT used. Re-run with --allow-positional-alignment to force it, accepting "
                "that the results are unreliable."
            ),
            pairs={},
            old_only={f"old[{i}]": s for i, s in enumerate(old_samples)},
            new_only={f"new[{i}]": s for i, s in enumerate(new_samples)},
            warnings=(
                "samples could not be aligned by any stable key; no sample-level "
                "comparison was performed",
            ),
        )

    n = min(len(old_samples), len(new_samples))
    return Alignment(
        method=AlignmentMethod.POSITIONAL,
        note="aligned on position in the sample list (WEAK -- explicitly requested)",
        pairs={f"pos[{i}]": (old_samples[i], new_samples[i]) for i in range(n)},
        old_only={f"old[{i}]": s for i, s in enumerate(old_samples) if i >= n},
        new_only={f"new[{i}]": s for i, s in enumerate(new_samples) if i >= n},
        warnings=(
            "WEAK ALIGNMENT: samples were matched by position in the sample list, not by any "
            "stable identifier. If the two runs ordered or shuffled samples differently, these "
            "pairings are wrong and every sample-level conclusion below is unreliable.",
        ),
    )
