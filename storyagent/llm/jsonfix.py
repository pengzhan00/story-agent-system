"""Recover a JSON object from what language models actually emit: think blocks, code fences, raw control
characters inside strings, prose around the JSON, and output cut off by the token limit."""
from __future__ import annotations

import json
import re
from typing import Any

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
_CTRL = {"\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f"}


def strip_think(text: str) -> str:
    return _THINK.sub("", text or "").strip()


def sanitize_control_chars(s: str) -> str:
    """Escape raw control characters that appear *inside* JSON string literals (illegal there)."""
    out: list[str] = []
    in_string = escape = False
    for ch in s:
        if escape:
            out.append(ch)
            escape = False
        elif in_string:
            if ch == "\\":
                out.append(ch)
                escape = True
            elif ch == '"':
                out.append(ch)
                in_string = False
            elif ch in _CTRL:
                out.append(_CTRL[ch])
            elif ord(ch) < 0x20:
                out.append(f"\\u{ord(ch):04x}")
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_string = True
            out.append(ch)
    return "".join(out)


def clean_candidate(text: str) -> str:
    text = strip_think(text)
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    return sanitize_control_chars(text.strip()).strip()


def extract_first_balanced(text: str) -> str:
    start = next((i for i, c in enumerate(text) if c in "{["), None)
    if start is None:
        return ""
    open_c, close_c = text[start], "}" if text[start] == "{" else "]"
    depth, in_string, escape = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == open_c:
            depth += 1
        elif ch == close_c:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:].strip()


def close_truncated(text: str) -> str:
    """Cut an unfinished document back to the last complete element and close the open brackets."""
    start = next((i for i, c in enumerate(text) if c in "{["), None)
    if start is None:
        return text
    stack: list[str] = []
    in_string = escape = False
    safe_pos, safe_stack = start, []
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()
                safe_pos, safe_stack = i + 1, list(stack)
                if not stack:
                    return text[start:i + 1]
        elif ch == "," and stack:
            safe_pos, safe_stack = i, list(stack)
    if not stack:
        return text[start:]
    return text[start:safe_pos] + "".join(reversed(safe_stack))


def parse_object(text: str) -> dict[str, Any] | None:
    """Best-effort: the first JSON *object* in ``text``, or ``None``."""
    cleaned = clean_candidate(text)
    candidates = [cleaned, extract_first_balanced(cleaned), close_truncated(cleaned)]
    seen: set[str] = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        try:
            value = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None
