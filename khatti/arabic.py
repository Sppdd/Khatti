"""Arabic text normalisation used before comparing reader outputs."""

from __future__ import annotations

import re
import unicodedata
from datetime import date

_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")
_TATWEEL = "ـ"
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_LETTERS = str.maketrans(
    {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ى": "ي",
        "ئ": "ي",
        "ؤ": "و",
        "ة": "ه",
        "ک": "ك",
        "ی": "ي",
    }
)
_SPACES = re.compile(r"\s+")


def to_western_digits(text: str) -> str:
    return text.translate(_DIGITS)


def normalize(text: str | None) -> str:
    """Canonical form for comparison: not for display."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = _DIACRITICS.sub("", text).replace(_TATWEEL, "")
    text = to_western_digits(text).translate(_LETTERS)
    return _SPACES.sub(" ", text).strip().casefold()


_DATE_PATTERNS = [
    (re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$"), ("y", "m", "d")),
    (re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$"), ("d", "m", "y")),
]


def parse_date(text: str | None) -> date | None:
    """Parse the date layouts found on Iraqi documents (Y-M-D or D/M/Y)."""
    if not text:
        return None
    s = to_western_digits(text).strip()
    for pattern, order in _DATE_PATTERNS:
        m = pattern.match(s)
        if m:
            parts = dict(zip(order, map(int, m.groups())))
            try:
                return date(parts["y"], parts["m"], parts["d"])
            except ValueError:
                return None
    return None


def similarity(a: str | None, b: str | None) -> float:
    """Normalised Levenshtein similarity in [0, 1]."""
    x, y = normalize(a), normalize(b)
    if x == y:
        return 1.0
    if not x or not y:
        return 0.0
    prev = list(range(len(y) + 1))
    for i, cx in enumerate(x, 1):
        cur = [i]
        for j, cy in enumerate(y, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (cx != cy)))
        prev = cur
    return 1 - prev[-1] / max(len(x), len(y))
