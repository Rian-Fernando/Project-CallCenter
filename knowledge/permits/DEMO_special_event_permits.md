---
title: Special Event and Block Party Permits (Placeholder)
department: permits
is_official: false
---

# DEMO DATA — NOT OFFICIAL VILLAGE INFORMATION

**This file is a placeholder created for the prototype. It intentionally
contains no schedules, fees, deadlines, or procedures, because inventing those
would be worse than having no document at all.**

## Why this file exists

The Village website's public pages did not yield a dedicated page for
non-construction permits (block parties, street closures, tag sales, film
shoots, dumpster/POD placement) during ingestion. This placeholder marks that
gap so it is visible rather than silent.

## What the assistant should do with this topic

Because no verified source is available, the correct behavior for questions
about special event permits is to **decline to answer specifics and offer to
connect the resident with the Village Clerk's office or the Building
Department**, whichever the Village designates.

The assistant must not state:

- application deadlines
- permit fees
- required forms or signatures
- insurance requirements
- approval timelines

## How a Village administrator fills this gap

1. Obtain the official permit procedure from Village staff.
2. Replace this file's content with the verified text.
3. Set `is_official: true` in the front matter above.
4. Add `source_url:` pointing at the official page or document.
5. Run `./scripts/ingest.sh`.

Until then, this document exists only to make the gap auditable.
