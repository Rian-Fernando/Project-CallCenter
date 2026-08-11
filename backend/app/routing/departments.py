"""Department registry, loaded from config/departments.yaml.

Single source of truth for the department taxonomy. Used by the intent router,
the crawler's URL classifier, the ingestion pipeline, and the API.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from app.core.config import settings

log = logging.getLogger(__name__)

GENERAL = "general"

# How much a department-name match is worth relative to a topic keyword.
# Set high because department names are near-definitive evidence.
NAME_KEYWORD_WEIGHT = 4.0


@dataclass(frozen=True)
class Department:
    id: str
    name: str
    description: str
    keywords: tuple[str, ...] = ()
    # Names the department is actually called on the Village website
    # ("Public Works", "Building Department"). Scored much higher than topic
    # keywords: a page titled "Building Department" is about the Building
    # Department, full stop. Omitting these caused real misclassification —
    # the Building Department page was landing in village_clerk because
    # "building" was not itself a keyword.
    name_keywords: tuple[str, ...] = ()
    phone: str | None = None
    email: str | None = None
    weight: float = 1.0

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": " ".join(self.description.split()),
            # Contact details are intentionally null until sourced from the
            # official Village directory — never guessed.
            "phone": self.phone,
            "email": self.email,
            "has_contact_info": bool(self.phone or self.email),
        }


@dataclass
class DepartmentRegistry:
    departments: dict[str, Department] = field(default_factory=dict)
    overrides: tuple[tuple[str, str], ...] = ()

    # -- lookup ------------------------------------------------------------
    def get(self, dept_id: str | None) -> Department:
        if dept_id and dept_id in self.departments:
            return self.departments[dept_id]
        return self.departments[GENERAL]

    def exists(self, dept_id: str | None) -> bool:
        return bool(dept_id) and dept_id in self.departments

    @property
    def ids(self) -> list[str]:
        return list(self.departments)

    def all(self) -> list[Department]:
        return list(self.departments.values())

    def name_of(self, dept_id: str | None) -> str:
        return self.get(dept_id).name

    # -- keyword scoring ---------------------------------------------------
    def score_text(self, text: str) -> dict[str, float]:
        """Score every department against free text by keyword match.

        Multi-word keywords score higher than single words, because "building
        permit" is far more diagnostic than "permit". Scores are weighted by
        each department's configured `weight`.
        """
        low = f" {re.sub(r'[^a-z0-9 ]+', ' ', text.lower())} "
        low = re.sub(r"\s+", " ", low)
        scores: dict[str, float] = {}

        for dept in self.departments.values():
            total = 0.0
            for kw in dept.keywords:
                if not kw:
                    continue
                # Word-boundary match avoids "car" matching inside "carpet".
                if re.search(rf"\b{re.escape(kw)}\b", low):
                    total += 1.0 + 0.75 * kw.count(" ")
            for kw in dept.name_keywords:
                if kw and re.search(rf"\b{re.escape(kw)}\b", low):
                    total += NAME_KEYWORD_WEIGHT
            if total:
                scores[dept.id] = round(total * dept.weight, 4)
        return scores

    def classify_content(
        self, *, title: str = "", slug: str = "", body: str = "",
        default: str = GENERAL,
    ) -> str:
        """Assign a department to a document from its title, URL slug, and text.

        Signals are weighted by how diagnostic they are. The title dominates
        (a page called "Sanitation" is a sanitation page), then the URL slug,
        then body text — and body text is truncated so a long tail of
        boilerplate cannot outvote a clear title.

        Lives here rather than in the crawler so that re-classification happens
        at ingestion time, letting rule changes take effect without re-fetching
        anything from the Village server.
        """
        combined: dict[str, float] = {}
        for weight, source in ((3.0, title), (2.0, slug), (1.0, body[:4000])):
            if not source:
                continue
            for dept_id, score in self.score_text(source).items():
                combined[dept_id] = combined.get(dept_id, 0.0) + score * weight

        if forced := self.check_overrides(f"{title} {slug}"):
            return forced
        if not combined:
            return default
        best = max(combined, key=combined.get)
        return best if combined[best] >= 2.0 else default

    def check_overrides(self, text: str) -> str | None:
        """Return a department forced by an exact disambiguating phrase."""
        low = re.sub(r"\s+", " ", text.lower())
        for phrase, dept_id in self.overrides:
            if phrase in low:
                return dept_id
        return None


def _load(path=None) -> DepartmentRegistry:
    cfg_path = path or (settings.config_dir / "departments.yaml")
    try:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        log.error("departments.yaml not found at %s — using a minimal fallback.", cfg_path)
        data = {"departments": [{"id": GENERAL, "name": "General Village Information",
                                 "description": "General questions."}]}

    departments: dict[str, Department] = {}
    for raw in data.get("departments", []):
        dept = Department(
            id=raw["id"],
            name=raw["name"],
            description=raw.get("description", ""),
            keywords=tuple(k.lower() for k in raw.get("keywords", []) or []),
            name_keywords=tuple(k.lower() for k in raw.get("name_keywords", []) or []),
            phone=raw.get("phone"),
            email=raw.get("email"),
            weight=float(raw.get("weight", 1.0)),
        )
        departments[dept.id] = dept

    if GENERAL not in departments:
        departments[GENERAL] = Department(
            id=GENERAL, name="General Village Information",
            description="General questions.",
        )

    # Longest phrases first so "parking permit" wins over "permit".
    overrides = tuple(sorted(
        ((o["phrase"].lower(), o["department"]) for o in data.get("overrides", [])
         if o.get("department") in departments),
        key=lambda p: -len(p[0]),
    ))

    log.info("Loaded %d departments, %d override phrases", len(departments), len(overrides))
    return DepartmentRegistry(departments=departments, overrides=overrides)


@lru_cache
def get_departments() -> DepartmentRegistry:
    return _load()
