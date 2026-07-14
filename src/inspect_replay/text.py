"""Sanitising untrusted text before it reaches a terminal.

An ``.eval`` log is an untrusted input. Its error messages, score values,
completions, and model names are attacker-controllable, and the text report
prints them. Without sanitising, a crafted log can embed ANSI/OSC escape
sequences that clear the screen, move the cursor, set the terminal title, or
print a forged green ``Evaluation: UNCHANGED`` line -- particularly damaging for
a tool whose whole output is a trust verdict.

The JSON output is protected separately, by ``ensure_ascii=True`` in
``json_output.to_json`` (which escapes DEL and the C1 range that
``json.dumps`` would otherwise pass through raw). This module protects the
human-readable text path.
"""

from __future__ import annotations

__all__ = ["sanitize"]

# C0 controls (0x00-0x1F) and DEL (0x7F), plus the C1 range (0x80-0x9F) which
# some terminals also interpret as control sequences. Tab, newline, and carriage
# return are included: the report composes its own layout, so a value must not
# smuggle in line breaks or tabs of its own.
_CONTROL = frozenset(range(0x00, 0x20)) | {0x7F} | frozenset(range(0x80, 0xA0))


def sanitize(text: str) -> str:
    """Replace every control character in ``text`` with a visible marker.

    Control characters become their Unicode 'control picture' (U+2400+) where
    one exists, so the reader can see that something was stripped rather than
    having it silently vanish. The result contains no character a terminal will
    act on.
    """
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if code in _CONTROL:
            # U+2400 CONTROL PICTURE FOR NULL .. U+241F for 0x1F; U+2421 for DEL.
            if code < 0x20:
                out.append(chr(0x2400 + code))
            elif code == 0x7F:
                out.append("␡")
            else:
                out.append(f"\\x{code:02x}")
        else:
            out.append(ch)
    return "".join(out)
