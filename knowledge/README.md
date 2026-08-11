# Garden City Knowledge Base

Everything the AI receptionist is allowed to know. If it isn't in here, the
system is designed to say *"I don't have enough verified information"* and
escalate — never to guess.

## Structure

```
knowledge/
├── _crawled/          Auto-generated cache of official gardencityny.net pages
│                      (gitignored — regenerate with scripts/crawl.py)
├── public_works/      Streets, snow, trees, water mains, storm drains
├── recreation/        Parks, pool, programs, senior center
├── building/          Permits, inspections, zoning, code enforcement
├── village_clerk/     Records, licenses, FOIL, elections
├── finance/           Taxes, water bills, payments
├── sanitation/        Garbage, recycling, yard waste, bulk pickup
├── parking/           Permits, meters, tickets, commuter parking
├── permits/           Non-construction permits: events, block parties
└── general/           Village Hall, hours, cross-department info
```

A file's **parent directory sets its department**. `sanitation/schedule.md`
becomes a Sanitation document.

## The two kinds of content

| | `is_official: true` | `is_official: false` |
|---|---|---|
| Where it comes from | Crawled from gardencityny.net, or verified by Village staff | Placeholder written for the prototype |
| Shown in UI as | Normal citation with live link | **DEMO DATA — NOT OFFICIAL VILLAGE INFORMATION** badge |
| Default for new files | — | ✅ this is the default |

**Local files default to `is_official: false`.** That default is deliberate and
should not be changed: an unlabeled document must never be presented to a
resident as official Village policy. Set the flag to `true` only after a person
has verified the content against an official source.

## Supported formats

`.md` · `.txt` · `.pdf` · `.html` · `.json` (structured FAQ)

### Markdown with front matter

```markdown
---
title: Leaf Collection Schedule
department: sanitation
is_official: false
source_url: https://www.gardencityny.net/204/Sanitation
---

# Leaf Collection

Content goes here...
```

### Structured FAQ JSON

```json
{
  "department": "parking",
  "is_official": false,
  "source_url": "https://www.gardencityny.net/234/Village-Parking-Information",
  "faqs": [
    { "question": "Where do I buy a parking permit?", "answer": "..." }
  ]
}
```

FAQ entries embed the question text alongside the answer, because residents
phrase requests much more like questions than like policy prose.

## Adding knowledge

**Preferred route — through the admin UI.** Answer a question in the review
queue at `/admin/review` and approve it. It is written to the database, indexed
into Qdrant, and becomes searchable immediately. The AI can never do this
itself; approval is always a human action (§15).

**Bulk route — files.** Drop files into the right department folder and run:

```bash
./scripts/ingest.sh
```

## Re-crawling official content

```bash
./scripts/crawl.sh          # polite: obeys robots.txt, 1 req/sec, caches
./scripts/ingest.sh         # rebuild the index
```

The crawler only visits pages listed in the Village sitemap, honors every
`Disallow` rule in robots.txt, identifies itself, and never runs parallel
requests. Please do not lower `CRAWL_DELAY_SECONDS`.
