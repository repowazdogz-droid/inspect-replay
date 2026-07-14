"""A detector for causal language, used by the honesty tests.

The tool's contract is that its own authored prose never asserts that one change
CAUSED an outcome -- only that things changed together. Enforcing that needs a
detector that catches causal VERBS and CONSTRUCTIONS, not a fixed list of two-
word phrases. An earlier version banned the literal string "caused by" and so
sailed straight past "caused the regression", "led to failures", "triggered
errors", and "drove accuracy down".

This is intentionally a blunt instrument that errs towards flagging. It is
applied to text the tool AUTHORS, where a false positive is a cheap prose fix.
It is NOT applied to verbatim quoted log content -- see the module docstring in
``report.py`` and the limits section of ``docs/assurance-boundary.md`` for why
that is out of scope.
"""

from __future__ import annotations

import re

# Each pattern names a way English asserts that A brought about B. Word
# boundaries keep them from firing inside longer words ("because" must not match
# inside "becausental", "cause" must not match "because"). Verb families cover
# their inflections.
_CAUSAL_PATTERNS: tuple[str, ...] = (
    r"\bcaus(?:e|es|ed|ing)\b",
    r"\bled to\b",
    r"\blead(?:s|ing)? to\b",
    r"\bresult(?:s|ed|ing)? in\b",
    r"\bdue to\b",
    r"\bbecause\b",
    r"\btrigger(?:s|ed|ing)?\b",
    r"\bd(?:rive|rives|rove|riving|riven)\b",
    r"\bproduc(?:e|es|ed|ing)\b",
    r"\bresponsible for\b",
    r"\bto blame\b",
    r"\bblame[ds]?\b",
    r"\bmade (?:it|them|the\b.*?) (?:fail|error|regress|pass|break)\b",
    r"\bexplain(?:s|ed|ing)?\b",
    r"\b(?:this|that|which) is why\b",
    r"\bthanks to\b",
    r"\bowing to\b",
    r"\bon account of\b",
    r"\bbrought about\b",
    r"\b(?:gave|gives|giving) rise to\b",
    r"\battributable to\b",
    r"\bstem(?:s|med|ming)? from\b",
    r"\bthe (?:reason|cause) (?:for|of)\b",
    r"\bfollows? from\b",
    r"\bmakes? .* (?:fail|pass|regress)\b",
    # A second wave, added after an independent reviewer found these getting
    # through. Each is scoped to avoid the tool's own honest prose -- note we do
    # NOT ban "the source of" (the residual observation says "the source of this
    # difference is not present"), and "contributed to" is a verb, distinct from
    # the noun "contributor" the tool legitimately uses.
    r"\bthe culprit\b",
    r"\baccounts? for\b",
    r"\bconsequence of\b",
    r"\b(?:is|are|was|were|sits|lies) behind\b",
    r"\bin response to\b",
    r"\bprecipitat(?:e|es|ed|ing)\b",
    r"\bcontribut(?:e|es|ed|ing) to\b",
    r"\btrac(?:e|es|ed) to\b",
    r"\bas a result of\b",
    r"\binstrumental in\b",
    r"\bis why\b",
)

_CAUSAL_RE = re.compile("|".join(_CAUSAL_PATTERNS), re.IGNORECASE)


def causal_language(text: str) -> str | None:
    """Return the first causal phrase found in ``text``, or ``None``.

    The return value is the matched substring, so a failing test can report
    exactly what tripped it.
    """
    match = _CAUSAL_RE.search(text)
    return match.group(0) if match else None
