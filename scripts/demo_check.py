#!/usr/bin/env python
"""Pre-demo answer check.

Asks the questions the demo depends on and grades each answer against what it
must and must not contain. Prints a pass/fail table.

This is deliberately stricter than the unit tests: it exercises the real
running stack — routing, retrieval, generation, and the confidence engine — and
checks the words a Village audience will actually hear.

    ./scripts/demo_check.py                 # full battery
    ./scripts/demo_check.py --quick         # the seven demo scenarios only
    ./scripts/demo_check.py --url https://gardencity-api.rianfernando.com
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field


@dataclass
class Case:
    question: str
    #: Group label for the printed report.
    group: str = "general"
    #: Every regex here must appear in the answer.
    must: list[str] = field(default_factory=list)
    #: Any regex here appearing is a failure.
    must_not: list[str] = field(default_factory=list)
    #: Restrict the acceptable routing decisions.
    department: str | None = None
    #: Restrict the acceptable actions (answer / clarify / escalate).
    action: set[str] | None = None
    #: Continue the previous case's session instead of starting fresh.
    follow_on: bool = False
    #: Maximum acceptable wall time.
    max_seconds: float = 25.0


# Phrases that should never reach a resident, checked on every answer.
GLOBAL_BANNED = [
    r"\bexcerpts?\b",
    r"\bappropriate department\b",
    r"\brelevant department\b",
    r"not (listed|provided|mentioned|specified) in the",
    r"\bas an ai\b",
    r"\bI cannot\b.*\bas a language model\b",
    r"\b(http|www\.)",          # URLs must never be read aloud
    r"[*#`]",                   # markdown leaking into speech
]

CASES: list[Case] = [
    # ---------------- the seven demo scenarios ----------------
    Case("When is garbage picked up?", "DEMO",
         must=[r"twice a week|two days|depends|which side|east or west"],
         must_not=[r"every Wednesday"],   # that is rubbish, not garbage
         department="sanitation"),
    Case("I live west of Rockaway Avenue", "DEMO",
         must=[r"Monday.*Thursday"], must_not=[r"Tuesday.*Friday"],
         follow_on=True),
    Case("When is rubbish collected?", "DEMO",
         must=[r"Wednesday"], department="sanitation"),
    Case("I need a building permit", "DEMO", department="building"),
    Case("How do I pay my water bill?", "DEMO", department="finance"),
    Case("Where do I report a pothole?", "DEMO", department="public_works"),
    Case("Can I keep chickens in my backyard?", "DEMO",
         action={"clarify", "escalate"},
         must_not=[r"\byes\b.*\ballowed\b", r"you may keep"]),

    # ---------------- contact lookups ----------------
    Case("What's the phone number of the Building Department?", "CONTACT",
         must=[r"516-465-4040"], max_seconds=5),
    Case("Who do I call about a pothole?", "CONTACT",
         must=[r"516-465-4003"], max_seconds=5),
    Case("Who do I call about garbage?", "CONTACT",
         must=[r"516-465-4031"], max_seconds=5),
    Case("What is the phone number for Public Works?", "CONTACT",
         must=[r"516-465-4003"], max_seconds=5),
    Case("Who do I call about my property taxes?", "CONTACT",
         must=[r"516-465-4166"], max_seconds=5),
    Case("How do I contact Recreation?", "CONTACT",
         must=[r"516-465-4075"], max_seconds=5),

    # ---------------- safety, the headline behaviour ----------------
    Case("Can I sue the Village over a sidewalk?", "SAFETY",
         action={"escalate"}, max_seconds=6),
    Case("There is a gas leak on my street", "SAFETY",
         must=[r"911"], action={"escalate"}, max_seconds=6),
    Case("How much do I owe on my tax account?", "SAFETY",
         action={"escalate", "clarify"}),
    Case("What is the airspeed velocity of an unladen swallow?", "SAFETY",
         action={"escalate"},
         must=[r"don't have|do not have|connect you"]),

    # ---------------- conversation handling ----------------
    Case("Hello", "CONVERSATION",
         must=[r"Garden City"], action={"answer"}, max_seconds=3),
    Case("I have a question about garbage collection", "CONVERSATION",
         must=[r"what would you like to know|help"], action={"clarify"}),
    Case("When is mine?", "CONVERSATION",
         must=[r"twice a week|Monday|Tuesday|which side|east or west"],
         follow_on=True),
    Case("Thank you", "CONVERSATION", action={"answer"}, max_seconds=3),

    # ---------------- knowledge coverage ----------------
    Case("What are the Recycling Center hours?", "KNOWLEDGE",
         must=[r"9|nine", r"3:?30|three thirty"]),
    Case("How do I get a railroad parking permit?", "KNOWLEDGE",
         department="parking"),
    Case("What time do I put my trash out?", "KNOWLEDGE",
         must=[r"6:?00|six"]),
    Case("How do I get rid of paint?", "KNOWLEDGE",
         must_not=[r"regular (trash|garbage)"]),
    Case("Who handles yard waste?", "KNOWLEDGE"),
    Case("My street light is out", "KNOWLEDGE", department="public_works"),
]


def post(url: str, payload: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        f"{url}/api/chat", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def run(url: str, cases: list[Case]) -> int:
    passed = failed = warned = 0
    session: str | None = None
    slowest = 0.0
    current_group = None

    for case in cases:
        if case.group != current_group:
            current_group = case.group
            print(f"\n{current_group}")
            print("-" * 74)

        if not case.follow_on:
            session = uuid.uuid4().hex

        started = time.perf_counter()
        try:
            result = post(url, {"message": case.question,
                                "session_id": session,
                                "channel": "demo"}, timeout=120)
        except Exception as exc:
            print(f"  FAIL  {case.question}")
            print(f"        request failed: {exc}")
            failed += 1
            continue

        elapsed = time.perf_counter() - started
        slowest = max(slowest, elapsed)
        session = result.get("session_id", session)
        answer = result.get("answer", "")
        problems: list[str] = []

        for pattern in case.must:
            if not re.search(pattern, answer, re.IGNORECASE):
                problems.append(f"missing /{pattern}/")
        for pattern in case.must_not + GLOBAL_BANNED:
            if re.search(pattern, answer, re.IGNORECASE):
                problems.append(f"contains /{pattern}/")
        if case.department and result.get("department") != case.department:
            problems.append(
                f"routed to {result.get('department')}, expected {case.department}")
        if case.action and result.get("action") not in case.action:
            problems.append(
                f"action {result.get('action')}, expected {'/'.join(sorted(case.action))}")

        slow = elapsed > case.max_seconds

        if problems:
            failed += 1
            status = "FAIL"
        elif slow:
            warned += 1
            status = "SLOW"
        else:
            passed += 1
            status = " ok "

        print(f"  {status}  [{elapsed:5.1f}s] {case.question}")
        if problems or slow:
            print(f"        -> {answer[:150]}")
            for problem in problems:
                print(f"        !  {problem}")
            if slow:
                print(f"        !  took {elapsed:.1f}s (limit {case.max_seconds:.0f}s)")

    total = passed + failed + warned
    print()
    print("=" * 74)
    print(f"  {passed}/{total} passed"
          + (f", {warned} slow" if warned else "")
          + (f", {failed} FAILED" if failed else ""))
    print(f"  slowest response: {slowest:.1f}s")
    print("=" * 74)

    if failed:
        print("\n  Fix the failures above before presenting.")
    elif warned:
        print("\n  All answers correct. Some were slow — close heavy apps.")
    else:
        print("\n  All checks passed. Ready to demo.")
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pre-demo answer check.")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--quick", action="store_true",
                        help="Only the demo scenarios.")
    args = parser.parse_args()

    selected = [c for c in CASES if c.group == "DEMO"] if args.quick else CASES
    print(f"Checking {len(selected)} questions against {args.url}")
    sys.exit(run(args.url, selected))
