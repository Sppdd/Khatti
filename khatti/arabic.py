"""Arabic (and Kurdish-aware) text handling: normalisation, dates, name matching.

Raw values are always stored as read; these normalised forms are for comparison only.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

# ---------------------------------------------------------------- normalisation

_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")
_TATWEEL = "ـ"
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_ALEF = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا"})
# Folded for matching only, and only for Arabic-script text that is not Kurdish.
_ARABIC_FOLD = str.maketrans({"ى": "ي", "ة": "ه", "ئ": "ي", "ؤ": "و", "ک": "ك", "ی": "ي"})
# Letters that mark Sorani Kurdish; when present, Kurdish letters are never folded.
KURDISH_LETTERS = frozenset("ڕڵێۆەڤپچگژ")
_ABD = re.compile(r"(^|\s)عبد\s+(?=\S)")
_SPACES = re.compile(r"\s+")
_ARABIC_CHAR = re.compile(r"[؀-ۿ]")
_LATIN_CHAR = re.compile(r"[A-Za-z]")


def to_western_digits(text: str) -> str:
    return text.translate(_DIGITS)


def is_kurdish(text: str) -> bool:
    return any(ch in KURDISH_LETTERS for ch in text)


def script_of(text: str) -> str:
    """'arabic', 'latin', 'mixed' or 'none' (Kurdish counts as arabic script)."""
    a, l = bool(_ARABIC_CHAR.search(text)), bool(_LATIN_CHAR.search(text))
    return "mixed" if a and l else "arabic" if a else "latin" if l else "none"


def normalize(text: str | None) -> str:
    """Canonical form for comparison: not for display or storage."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = _DIACRITICS.sub("", text).replace(_TATWEEL, "")
    text = to_western_digits(text).translate(_ALEF)
    if not is_kurdish(text):
        text = text.translate(_ARABIC_FOLD)
    text = _SPACES.sub(" ", text).strip()
    text = _ABD.sub(r"\1عبد", text)
    return text.casefold()


# ---------------------------------------------------------------- similarity

def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a or not b:
        return max(len(a), len(b))
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def similarity(a: str | None, b: str | None) -> float:
    """Normalised Levenshtein similarity in [0, 1] over normalised text."""
    x, y = normalize(a), normalize(b)
    if x == y:
        return 1.0
    if not x or not y:
        return 0.0
    return 1 - levenshtein(x, y) / max(len(x), len(y))


def cer(reference: str, hypothesis: str) -> float:
    """Character error rate over normalised text."""
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return levenshtein(ref, hyp) / len(ref)


def jaro_winkler(a: str, b: str, prefix_scale: float = 0.1) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    window = max(max(len(a), len(b)) // 2 - 1, 0)
    a_hit, b_hit = [False] * len(a), [False] * len(b)
    matches = 0
    for i, ca in enumerate(a):
        for j in range(max(0, i - window), min(len(b), i + window + 1)):
            if not b_hit[j] and b[j] == ca:
                a_hit[i] = b_hit[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    bs = [b[j] for j in range(len(b)) if b_hit[j]]
    transpositions = sum(ca != bs[k] for k, ca in enumerate(c for i, c in enumerate(a) if a_hit[i])) / 2
    jaro = (matches / len(a) + matches / len(b) + (matches - transpositions) / matches) / 3
    prefix = 0
    for ca, cb in zip(a[:4], b[:4]):
        if ca != cb:
            break
        prefix += 1
    return jaro + prefix * prefix_scale * (1 - jaro)


# ---------------------------------------------------------------- names

_TRANSLIT = str.maketrans(
    {
        "ا": "a", "ب": "b", "ت": "t", "ث": "th", "ج": "j", "ح": "h", "خ": "kh", "د": "d", "ذ": "dh",
        "ر": "r", "ز": "z", "س": "s", "ش": "sh", "ص": "s", "ض": "d", "ط": "t", "ظ": "dh", "ع": "",
        "غ": "gh", "ف": "f", "ق": "q", "ك": "k", "ل": "l", "م": "m", "ن": "n", "ه": "h", "و": "w",
        "ي": "y", "ء": "",
    }
)
_SKELETON_DROP = re.compile(r"[aeiouyhw'\-\s]")
_REPEATS = re.compile(r"(.)\1+")


def _skeleton(latin: str) -> str:
    """Consonant skeleton: tolerant of vowel/transliteration variation (Hussein ~ حسين)."""
    s = latin.casefold().replace("q", "k").replace("dh", "d").replace("th", "t").replace("kh", "k")
    s = s.replace("gh", "g").replace("sh", "s").replace("j", "g").replace("z", "d")
    return _REPEATS.sub(r"\1", _SKELETON_DROP.sub("", s))


def transliterate(arabic: str) -> str:
    return normalize(arabic).translate(_TRANSLIT)


@dataclass(frozen=True)
class NameMatch:
    status: str  # match | partial_match | mismatch | transliteration_match | skipped
    score: float
    detail: str = ""


def name_tokens(name: str) -> list[str]:
    return [t for t in normalize(name).split(" ") if t]


def match_names(a: str | None, b: str | None, threshold: float = 0.92, translit_threshold: float = 0.85) -> NameMatch:
    """Order-preserving token alignment of Iraqi multi-part names.

    - The given name (first token) must match exactly.
    - Every token of the shorter name must align, in order, to a token of the longer one
      with Jaro-Winkler >= threshold. Tokens missing from the shorter name (e.g. a license
      that omits the grandfather name) give partial_match, never match.
    - If one side is Latin script, both are compared as consonant skeletons; the result is
      at best transliteration_match, which always routes to a human.
    """
    if not a or not b:
        return NameMatch("skipped", 0.0, "name missing")
    sa, sb = script_of(a), script_of(b)
    translit = {sa, sb} == {"arabic", "latin"}
    if translit:
        ar, la = (a, b) if sa == "arabic" else (b, a)
        ta = [_skeleton(t) for t in transliterate(ar).split(" ") if t]
        tb = [_skeleton(t) for t in la.split() if t]
        thr = translit_threshold
    else:
        ta, tb = name_tokens(a), name_tokens(b)
        thr = threshold
    ta, tb = [t for t in ta if t], [t for t in tb if t]
    if not ta or not tb:
        return NameMatch("skipped", 0.0, "name empty after normalisation")
    if (ta[0] != tb[0]) if not translit else jaro_winkler(ta[0], tb[0]) < thr:
        return NameMatch("mismatch", 0.0, "given name differs")

    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    # DP: best in-order alignment of every short token onto distinct long tokens.
    n, m = len(short), len(long_)
    NEG = float("-inf")
    best = [[NEG] * (m + 1) for _ in range(n + 1)]
    back: dict[tuple[int, int], tuple[int, int, bool]] = {}
    for j in range(m + 1):
        best[0][j] = 0.0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            skip = best[i][j - 1]
            s = jaro_winkler(short[i - 1], long_[j - 1])
            take = best[i - 1][j - 1] + s if s >= thr and best[i - 1][j - 1] > NEG else NEG
            if take >= skip:
                best[i][j], back[(i, j)] = take, (i - 1, j - 1, True)
            else:
                best[i][j], back[(i, j)] = skip, (i, j - 1, False)
    if best[n][m] == NEG:
        return NameMatch("mismatch", 0.0, "name tokens do not align")

    matched_long: set[int] = set()
    i, j = n, m
    while i > 0 and j > 0:
        pi, pj, took = back[(i, j)]
        if took:
            matched_long.add(j - 1)
        i, j = pi, pj
    score = best[n][m] / m
    if translit:
        return NameMatch("transliteration_match", score, "matched only via transliteration")
    if n == m:
        return NameMatch("match", score)
    missing = [long_[k] for k in range(m) if k not in matched_long]
    return NameMatch("partial_match", score, f"shorter name omits: {' '.join(missing)}")


# ---------------------------------------------------------------- dates

_GREG_MONTHS = {
    # Iraqi / Levantine (Syriac) names
    "كانون الثاني": 1, "شباط": 2, "اذار": 3, "نيسان": 4, "ايار": 5, "حزيران": 6,
    "تموز": 7, "اب": 8, "ايلول": 9, "تشرين الاول": 10, "تشرين الثاني": 11, "كانون الاول": 12,
    # Egyptian / Gulf names
    "يناير": 1, "فبراير": 2, "مارس": 3, "ابريل": 4, "مايو": 5, "يونيو": 6, "يونيه": 6,
    "يوليو": 7, "يوليه": 7, "اغسطس": 8, "سبتمبر": 9, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
}
_HIJRI_MONTHS = {
    "محرم": 1, "صفر": 2, "ربيع الاول": 3, "ربيع الثاني": 4, "ربيع الاخر": 4, "جمادي الاولي": 5,
    "جمادي الاخره": 6, "جمادي الثانيه": 6, "رجب": 7, "شعبان": 8, "رمضان": 9, "شوال": 10,
    "ذو القعده": 11, "ذو الحجه": 12,
}
_HIJRI_MARKER = re.compile(r"هـ|ه\.?\s*$|\bAH\b", re.IGNORECASE)
_NUMERIC = [
    (re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$"), ("y", "m", "d")),
    (re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$"), ("d", "m", "y")),
]
HIJRI_YEARS = range(1343, 1501)  # hijridate's Umm al-Qura table range


@dataclass(frozen=True)
class ParsedDate:
    value: date
    calendar: str  # gregorian | hijri
    converted: bool  # True when converted from Hijri


def _month_lookup(text: str, table: dict[str, int]) -> tuple[int, str] | None:
    # Longest names first so "تشرين الثاني" wins over a bare prefix.
    for name in sorted(table, key=len, reverse=True):
        if name in text:
            return table[name], name
    return None


def _build(y: int, m: int, d: int, hijri: bool) -> ParsedDate | None:
    try:
        if hijri:
            from hijridate import Hijri

            g = Hijri(y, m, d).to_gregorian()
            return ParsedDate(date(g.year, g.month, g.day), "hijri", True)
        return ParsedDate(date(y, m, d), "gregorian", False)
    except (ValueError, OverflowError):
        return None


def parse_date(text: str | None) -> ParsedDate | None:
    """Parse YYYY/MM/DD, DD/MM/YYYY, Arabic-Indic digits, and Arabic month names (Gregorian or Hijri)."""
    if not text:
        return None
    raw = to_western_digits(text).strip()
    marker = bool(_HIJRI_MARKER.search(raw))
    clean = _HIJRI_MARKER.sub("", raw).strip()

    for pattern, order in _NUMERIC:
        m = pattern.match(clean)
        if m:
            p = dict(zip(order, map(int, m.groups())))
            return _build(p["y"], p["m"], p["d"], marker or p["y"] in HIJRI_YEARS)

    norm = normalize(clean)
    hit = _month_lookup(norm, _HIJRI_MONTHS)
    hijri_month = hit is not None
    if not hit:
        hit = _month_lookup(norm, _GREG_MONTHS)
    if hit:
        month, name = hit
        nums = [int(n) for n in re.findall(r"\d+", norm.replace(name, " "))]
        if len(nums) == 2:
            day, year = (nums[0], nums[1]) if nums[1] > 31 else (nums[1], nums[0])
            return _build(year, month, day, marker or hijri_month or year in HIJRI_YEARS)
    return None
