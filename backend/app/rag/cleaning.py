"""Text cleaning — the stage between extraction and chunking.

Kept separate from extraction on purpose: cleaning rules change often as new
boilerplate is discovered, and running them here means they apply to already
cached content without re-fetching anything from the Village server.
"""

from __future__ import annotations

import re

# Lines that carry no information. Matched against a whole stripped line.
BOILERPLATE_LINES = [
    re.compile(p, re.IGNORECASE) for p in (
        # The Village CMS ships unedited placeholder answers in its FAQ module.
        r"^answer goes here\.*$",
        r"^(question|answer) goes here\.*$",
        r"^lorem ipsum.*$",
        r"^home\s*[-–—]\s*faqs?$",
        r"^(faqs?|frequently asked questions)$",
        r"^(read more|learn more|click here|view all|show all|see all|more info)\.*$",
        r"^(previous|next|first|last|back|forward)$",
        r"^page \d+( of \d+)?$",
        r"^\W{0,3}$",
        r"^(select a (question|category)|browse the available categories).*$",
        r"^(share|print|email|translate|font size|text size)$",
        r"^(loading|please wait)\.*$",
        r"^(sign in|log in|register|subscribe)$",
        r"^skip to.*$",
        r"^\d{1,3}$",
    )
]

# Phrases removed inline rather than dropping the whole line.
INLINE_NOISE = [
    (re.compile(r"\[\s*(pdf|doc|docx|xls|xlsx)\s*\]", re.I), ""),
    (re.compile(r"\(opens? in (a )?new (window|tab)\)", re.I), ""),
    (re.compile(r"\bclick here\b", re.I), ""),
    (re.compile(r"[​‌‍﻿]"), ""),   # zero-width chars
    (re.compile(r"[ ]"), " "),                     # non-breaking space
]


def clean_text(text: str, *, min_line_length: int = 2) -> str:
    """Remove boilerplate, collapse whitespace, and drop repeated lines."""
    if not text:
        return ""

    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        for pattern, replacement in INLINE_NOISE:
            line = pattern.sub(replacement, line)
        line = re.sub(r"[ \t]+", " ", line).strip()

        if len(line) < min_line_length:
            continue
        if any(p.match(line) for p in BOILERPLATE_LINES):
            continue
        lines.append(line)

    # Drop consecutive duplicates (repeated nav labels, headers).
    deduped: list[str] = []
    for line in lines:
        if not deduped or deduped[-1].lower() != line.lower():
            deduped.append(line)

    # Drop lines that repeat many times across the document — on this CMS a
    # line appearing 5+ times is navigation chrome, not content.
    counts: dict[str, int] = {}
    for line in deduped:
        counts[line.lower()] = counts.get(line.lower(), 0) + 1
    result = [
        line for line in deduped
        if counts[line.lower()] < 5 or len(line) > 80
    ]

    return re.sub(r"\n{3,}", "\n\n", "\n".join(result)).strip()


def looks_substantive(text: str, *, min_chars: int = 150, min_words: int = 25) -> bool:
    """Is this document worth embedding at all?

    Filters out stubs that would otherwise pollute retrieval with near-empty
    matches that score well on short queries.
    """
    stripped = text.strip()
    return len(stripped) >= min_chars and len(stripped.split()) >= min_words
