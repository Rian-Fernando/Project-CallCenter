"""Spoken-form text normalization.

Speech engines read raw text literally. Kokoro pronounces "6:00 a.m." as
"six, zero, zero, a m" — technically the characters, but not what a person
says. Municipal answers are full of times, fees, and date ranges, so this
matters on almost every real response.

Normalization happens on the way INTO the speech engine only. The transcript
shown on screen keeps the original text, because "6:00 a.m." is what a resident
should read and what the Village document actually says.
"""

from __future__ import annotations

import re

ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
        "eighty", "ninety"]


def _two_digit_words(n: int) -> str:
    """0-99 as words. Used for clock minutes, where digits read badly."""
    if n < 20:
        return ONES[n]
    tens, ones = divmod(n, 10)
    return TENS[tens] + (f" {ONES[ones]}" if ones else "")


def _time(match: re.Match) -> str:
    """Render a clock time the way a person says it out loud.

    "6:00 a.m." becomes "six in the morning", not "six A M". Spelled-out
    meridiems sound clinical over the phone, and a resident half-listening
    parses "in the morning" far more reliably than two spoken letters.
    """
    hour = int(match.group("h"))
    minute = int(match.group("m"))
    meridiem = (match.group("ap") or "").replace(".", "").replace(" ", "").upper()

    # Noon and midnight have their own words; "twelve P M" is needlessly formal.
    if meridiem and minute == 0 and hour == 12:
        return "noon" if meridiem.startswith("P") else "midnight"

    if minute == 0:
        clock = ONES[hour] if hour < 20 else str(hour)
    elif minute < 10:
        # "6:05" is "six oh five", never "six five".
        clock = f"{ONES[hour] if hour < 20 else hour} oh {ONES[minute]}"
    else:
        clock = f"{ONES[hour] if hour < 20 else hour} {_two_digit_words(minute)}"

    if not meridiem:
        return f"{clock} o'clock" if minute == 0 else clock

    if meridiem.startswith("A"):
        return f"{clock} in the morning"
    # 12-4 pm reads as afternoon; 5 pm onward as evening.
    return f"{clock} in the {'afternoon' if hour == 12 or hour < 5 else 'evening'}"


def _digits(text: str) -> str:
    """Spell out digits individually: "516" -> "five one six".

    Speech engines read digit runs as magnitudes ("five hundred sixteen"),
    which is wrong for anything a caller is meant to write down — phone
    numbers, extensions, permit numbers.
    """
    return " ".join(ONES[int(ch)] for ch in text if ch.isdigit())


def _phone(match: re.Match) -> str:
    area, prefix, line = match.groups()
    # Commas give the engine a natural pause between groups.
    return f"{_digits(area)}, {_digits(prefix)}, {_digits(line)}"


def _extension(match: re.Match) -> str:
    return f"extension {_digits(match.group(1))}"


def _money(match: re.Match) -> str:
    dollars = int(match.group("d").replace(",", ""))
    cents = match.group("c")
    unit = "dollar" if dollars == 1 else "dollars"
    if not cents or int(cents) == 0:
        return f"{dollars} {unit}"
    return f"{dollars} {unit} and {int(cents)} cents"


# Order matters: times before general numbers, money before decimals.
_RULES: list[tuple[re.Pattern[str], object]] = [
    # 6:00 a.m. / 6:30PM / 12:05 p.m.
    (re.compile(r"\b(?P<h>1[0-2]|0?[1-9]):(?P<m>[0-5][0-9])\s*(?P<ap>[ap]\.?\s?m\.?)?",
                re.IGNORECASE), _time),
    # $12.50 / $1,200
    (re.compile(r"\$\s*(?P<d>[\d,]+)(?:\.(?P<c>\d{2}))?"), _money),

    # Bare meridiems that survived the time rule.
    (re.compile(r"\ba\.m\.?", re.IGNORECASE), "A M"),
    (re.compile(r"\bp\.m\.?", re.IGNORECASE), "P M"),

    # Phone numbers BEFORE the range rule below, or "516-465-4051" becomes
    # "516 to 465 to 4051". Spoken digit by digit — a caller writing a number
    # down needs "five one six", not "five hundred sixteen".
    (re.compile(r"\b(\d{3})[-.\s](\d{3})[-.\s](\d{4})\b"), _phone),
    # Extensions and 4-5 digit internal numbers, same reasoning.
    (re.compile(r"\b(?:ext|extension)\.?\s*(\d{3,5})\b", re.IGNORECASE), _extension),

    # Ranges read as "to" rather than a dash.
    (re.compile(r"(?<=[A-Za-z0-9])\s*[–—-]\s*(?=[A-Za-z0-9])"), " to "),

    # Common municipal abbreviations.
    (re.compile(r"\bSt\.(?=\s+[A-Z])"), "Saint"),
    (re.compile(r"\bAve\b\.?", re.IGNORECASE), "Avenue"),
    (re.compile(r"\bBlvd\b\.?", re.IGNORECASE), "Boulevard"),
    (re.compile(r"\bRd\b\.?", re.IGNORECASE), "Road"),
    (re.compile(r"\bDept\b\.?", re.IGNORECASE), "Department"),
    (re.compile(r"\bApt\b\.?", re.IGNORECASE), "Apartment"),
    (re.compile(r"\bMon\b\.?", re.IGNORECASE), "Monday"),
    (re.compile(r"\bTue(s)?\b\.?", re.IGNORECASE), "Tuesday"),
    (re.compile(r"\bWed\b\.?", re.IGNORECASE), "Wednesday"),
    (re.compile(r"\bThur(s)?\b\.?", re.IGNORECASE), "Thursday"),
    (re.compile(r"\bFri\b\.?", re.IGNORECASE), "Friday"),
    (re.compile(r"\bSat\b\.?", re.IGNORECASE), "Saturday"),
    (re.compile(r"\bSun\b\.?", re.IGNORECASE), "Sunday"),

    # Symbols that would otherwise be read as their names or skipped.
    (re.compile(r"\s*&\s*"), " and "),
    (re.compile(r"(?<=\d)\s*%"), " percent"),
    (re.compile(r"(?<=\d)\s*#"), " number "),
    (re.compile(r"\bw/\b", re.IGNORECASE), "with"),
    (re.compile(r"\be\.g\.", re.IGNORECASE), "for example"),
    (re.compile(r"\bi\.e\.", re.IGNORECASE), "that is"),
    (re.compile(r"\betc\.", re.IGNORECASE), "et cetera"),
    (re.compile(r"\bvs\.?\b", re.IGNORECASE), "versus"),
    (re.compile(r"\bapprox\.", re.IGNORECASE), "approximately"),

    # Leftover markup that should never reach the engine.
    (re.compile(r"https?://\S+"), "the Village website"),
    (re.compile(r"[*_`#|]+"), " "),
    (re.compile(r"\s{2,}"), " "),
]


def normalize_for_speech(text: str) -> str:
    """Rewrite text so a speech engine says it the way a person would.

    Idempotent and safe on already-natural text.
    """
    if not text:
        return ""
    out = text
    for pattern, replacement in _RULES:
        out = pattern.sub(replacement, out)  # type: ignore[arg-type]
    return out.strip()
