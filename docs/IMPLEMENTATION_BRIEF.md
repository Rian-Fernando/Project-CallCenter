# Implementation Brief — For the Village Conversation

What to ask them, what they'll ask you, and what it actually costs.

---

## Part 1 — Questions to ask the Village

Ask these before proposing anything. The answers determine cost, timeline, and
whether phone integration is a week or a quarter.

### About their phone system (ask first — everything depends on this)

1. **"What phone system does the Village use today?"**
   Listen for: Cisco, Avaya, Mitel, RingCentral, 8x8, Vonage, Zoom Phone, Teams
   Phone, or "we're not sure."

2. **"Is it on-premise hardware, or cloud/hosted?"**
   Cloud (RingCentral, 8x8, Zoom, Teams) is much easier — most support SIP
   forwarding out of the box. On-premise PBX means involving whoever maintains
   it.

3. **"Who manages it — internal IT, or an outside vendor?"**
   Determines who you'll actually be coordinating with, and whether changes need
   a contract amendment.

4. **"What's the main number residents call, and what happens now?"**
   Auto-attendant? Live receptionist? Voicemail after hours?

5. **"Can that system forward calls to an external number or SIP address?"**
   ⭐ **The single most important technical question.** If yes, integration is
   straightforward. If no, it's a bigger project.

6. **"Roughly how many calls a day? What are the top five reasons people call?"**
   Sizes the deployment and tells you which department to pilot.

7. **"When are you busiest — and what happens to calls after hours?"**
   After-hours coverage is often the easiest win: the AI answers when nobody
   else can.

### About their content

8. **"Is the website the authoritative source, or does staff know things that
   aren't published?"**
   Almost always the latter. That gap is the real project.

9. **"Who owns keeping department information current, and how often does it
   change?"**

10. **"Do you have a documented FAQ, or is it in people's heads?"**

11. **"How do you use GoGov today — service requests, FAQs, notifications?"**

### About constraints

12. **"Are there policies about resident data leaving Village systems?"**
    ⭐ Determines local vs. cloud AI. Ask early — it can rule out hosted models
    entirely.

13. **"Do call recordings or transcripts count as public records under your
    retention schedule?"**
    A question for the Village Clerk. Get it on their radar.

14. **"Who would need to approve this — Board of Trustees, Village
    Administrator, IT?"**

15. **"Is there a budget cycle this would need to fit into?"**

---

## Part 2 — What phone system they'd need

**Good news: almost certainly no new hardware.**

### If they're on a cloud phone system (RingCentral, 8x8, Zoom Phone, Teams)

**Easiest case.** These support forwarding a number or extension to an external
SIP address. You point one number at the AI and it works. Typically a few hours
of configuration.

### If they're on a modern on-premise PBX (Cisco, Avaya, Mitel)

Workable. These support SIP trunking. Their IT or vendor configures a route to
the AI. More coordination, but no replacement.

### If they're on very old hardware (analog, TDM, no SIP)

The AI would need its own number instead, published as a separate line —
"call this number for automated help with common questions." Avoids touching
their system entirely. Also a reasonable *first* step regardless.

### The honest recommendation for a pilot

**Don't integrate at all initially.** Get a dedicated number, publish it for
one department, and route to the AI. That:

- Requires zero changes to Village infrastructure
- Contains the risk — the main line is untouched
- Gives real usage data before anyone commits
- Can be turned off instantly

Then integrate properly once it's proven.

---

## Part 3 — Can the free version actually be deployed?

**Partly, and it's worth being precise about which parts.**

### What genuinely stays free forever

| Component | Notes |
|---|---|
| The software | All of it. Open source, no licensing |
| Local models (Ollama, Whisper, Kokoro) | Free regardless of call volume |
| Vector database (Qdrant) | Free, self-hosted |
| PostgreSQL | Free |
| Frontend hosting | Vercel free tier is sufficient |

**Marginal cost per call: $0.** That's genuinely unusual and worth emphasizing —
most vendors charge per minute.

### What can never be free

| Component | Why | Cost |
|---|---|---|
| A phone number | Carriers charge for connectivity | ~$1–2/month |
| Inbound minutes | Same | ~$0.0085–0.013/min |
| A server that's always on | Something must host it | $0 if Village hardware; ~$70–250/mo cloud |
| Staff time | Verifying content, reviewing questions | The largest real cost |

### The honest verdict

**The free stack can run in production**, on a Village server, with no per-call
AI cost. What it needs first is the work in `SECURITY_ROADMAP.md`:
authentication, encryption, audit logging, PII handling. That's engineering
time, not licensing.

**What "free" does not mean:** free of effort. Village staff must verify every
answer the system can give. That's unavoidable with any vendor, and it's the
step most likely to be underestimated.

---

## Part 4 — Cost breakdown

### Option A — Fully local, Village-hosted (maximum privacy)

| Item | One-time | Monthly |
|---|---|---|
| Server (or existing Village hardware) | $0–2,500 | $0 |
| Software | $0 | $0 |
| AI usage | $0 | **$0** |
| Phone number + ~3,000 min/mo | — | ~$40 |
| **Total** | **$0–2,500** | **~$40** |

Best when policy forbids resident data leaving Village systems. Needs someone
to maintain the server.

### Option B — Hybrid (recommended starting point)

Local models for routine questions; a hosted model only for hard ones.

| Item | Monthly |
|---|---|
| Cloud VM | ~$80 |
| Hosted LLM (~20% of turns) | ~$15–40 |
| Managed database | ~$25 |
| Phone | ~$40 |
| **Total** | **~$160–185** |

Faster than fully local, far cheaper than fully hosted. Requires a data
processing agreement with the model vendor.

### Option C — Fully managed (fastest, least control)

| Item | Monthly |
|---|---|
| Hosted LLM | ~$60–150 |
| Managed speech-to-text + text-to-speech | ~$90–150 |
| Container hosting + database + vector DB | ~$110 |
| Phone | ~$40 |
| **Total** | **~$300–450** |

Assumes ~100 calls/day at ~3 minutes.

### For comparison

Commercial AI receptionist platforms typically run **$0.10–0.25 per minute**.
At 100 calls/day × 3 min ≈ 9,000 min/month, that's **$900–2,250/month** — and
you don't control the knowledge base or the refusal behavior.

**The pitch:** comparable capability at roughly a tenth of the cost, with the
Village owning the system.

### The cost people forget

| Item | Estimate |
|---|---|
| Staff verifying content | 20–40 hours up front |
| Ongoing review of unanswered questions | 1–2 hours/week |
| Engineering to production-ready | 6–10 weeks |
| Legal/records review | A few hours of counsel time |

---

## Part 5 — Proposed plan to present

**Phase 0 — Today.** Prototype exists. Answers from the live Village site,
refuses when it doesn't know, routes to nine departments.

**Phase 1 — Content validation (2–4 weeks, mostly Village staff).**
Staff verify what it can answer, fill gaps, populate department contacts.
*Deliverable: a knowledge base the Village stands behind.*
Highest value, least technical, and it de-risks everything after.

**Phase 2 — Hardening (3–4 weeks, engineering).**
Authentication, encryption, audit logging, PII redaction, backups, monitoring.
*Deliverable: a system that can legally handle real calls.*

**Phase 3 — Phone pilot (2–3 weeks).**
A dedicated number for **one** department — sanitation is ideal: highest volume,
factual answers, low stakes. Human fallback always available.
*Deliverable: real residents, real data, contained risk.*

**Phase 4 — Measured expansion.**
Add departments only as each meets an accuracy bar. Weekly review of unanswered
questions.

**Total to a live pilot: roughly 8–11 weeks**, most of it Village staff time
rather than engineering.

---

## Part 6 — Questions they will ask, and how to answer

**"What if it gives someone wrong information?"**
> The main design decision was refusing over guessing. It only answers when
> retrieval strongly supports it, and a second check verifies the answer against
> the source before it's spoken. When it can't, it hands off. Every answer cites
> its source, so anything wrong is traceable to a page you can fix. And it's
> never the only option — a person is always reachable.

**"Is this replacing staff?"**
> No. It handles the repetitive questions — hours, schedules, where to apply —
> so staff spend time on things that need judgment. It routes *to* staff; it
> can't do their work. Most Villages find it useful for after-hours and peak
> times, when the alternative is voicemail.

**"Where does resident data go?"**
> Right now, nowhere. Everything runs on this laptop. In production, that's
> your choice — fully local means no data leaves Village systems. There's no
> caller name, phone number, or address stored anywhere, and transcripts delete
> after seven days by default.

**"How does it stay current?"**
> Two ways. It re-reads the Village website on a schedule. And when staff answer
> something it couldn't, they approve that answer and it's available immediately.
> The system can't change its own knowledge — only your people can.

**"What happens when it breaks?"**
> Calls route to a person, exactly as today. It's an addition to the phone tree,
> not a replacement for it. There's a kill switch.

**"How much?"**
> Depends on where it runs. Roughly $40/month fully local on Village hardware,
> to about $400/month fully managed. Commercial alternatives run $900–2,000+ at
> your volume. The bigger cost is staff time verifying content.

**"How long?"**
> About 8–11 weeks to a live pilot on one department. Most of that is your team
> confirming the information is right, not engineering.

**"Can it handle Spanish?"** *(likely in Nassau County)*
> The speech and language models support it. It would need Spanish content and
> testing — a real piece of work, not a checkbox, but well within scope.

**"What about accessibility?"**
> Important and not yet addressed. Callers using relay services or with atypical
> speech need a guaranteed path to a person. That's a requirement for any
> deployment, and worth naming as an open item rather than glossing over.

**"Who else is doing this?"**
> Municipal AI assistants are early. Worth answering honestly: this is a
> prototype built to show what's feasible, not a product with a customer list.
> The advantage is the Village would own it.

**"Can we see it fail?"**
> Yes — say yes enthusiastically. Ask it something obscure and let them watch it
> decline. That demo is more convincing than any successful answer.

---

## Part 7 — What to be upfront about

Credibility comes from naming these before they're found:

1. **Not production-ready.** No authentication, no encryption at rest, no audit trail.
2. **Content is unverified.** Read off the public website; nobody at the Village has confirmed it.
3. **Department phone numbers are deliberately blank.** They weren't guessed. They need to come from the official directory.
4. **Response time is 3–8 seconds locally.** A hosted model is under a second. That's a cost decision.
5. **No phone number yet.** Browser only. Telephony is a known, scoped piece of work.
6. **One caller at a time** in the current setup. Concurrency needs a server.

Saying these first makes everything else you claim more believable.
