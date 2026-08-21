# Phone Call Prep — Village of Garden City

No screen, no demo. Everything lands on how clearly you describe it and how
honestly you handle questions. Keep this open during the call.

---

## Your 30-second opener

Have this ready verbatim. You will be asked "so what is it?" in the first minute.

> "I built a working prototype of an AI receptionist for the Village. It answers
> resident questions using the Village's own published information — garbage
> schedules, permits, parking — and routes calls to the right department. The
> key thing is that it's built to refuse rather than guess: if the answer isn't
> in verified Village information, it says so and hands off to a person instead
> of making something up. It runs entirely on a local machine right now, so
> there's no per-call cost and no resident data leaving the building."

Then stop talking. Let them ask.

## Three things to say early

1. **"It refuses rather than guesses."** Your differentiator. Say it in the first two minutes.
2. **"Every answer cites the Village page it came from."** Verifiable, not a black box.
3. **"It's a prototype, not a product."** Saying this first makes everything else credible.

## Three things NOT to say

- ❌ "It's ready to go" — it isn't
- ❌ "It's 100% accurate" — nothing is, and they'll test it
- ❌ Any number you haven't thought through — "let me get back to you with real figures" is a strong answer

---

## GoGov — the section to read twice

They will likely raise it, because they already pay for it.

### What you must know before answering

**GoGov does not publish a public API specification.** That is not a guess — it
was checked. Any integration starts with a conversation between the Village and
GoGov, not with engineering.

Say it plainly: *"I built the integration as a clearly-labeled mock, because I
won't invent endpoints that might not exist. What it would actually take
depends entirely on what GoGov will give the Village access to."*

That answer builds more trust than pretending it's solved.

### What is genuinely hard

| Difficulty | Why it matters |
|---|---|
| **No public API** | Access is a commercial conversation. GoGov may charge for it, restrict it, or not offer it on the Village's tier. **This is the gating item — everything else is downstream.** |
| **No sandbox** | Testing against production would create real service requests for real residents. A test environment must be part of the agreement. |
| **Status lookups need identity** | "What's the status of my request?" requires knowing who is calling. That means collecting identifying information over the phone — a privacy and records decision, not a technical one. |
| **Photos** | Most service requests want a photo. A phone call cannot provide one. Best case is a texted link so the resident finishes on their phone. |
| **Duplicate requests** | If a call drops mid-submission, does it create a second ticket? Needs idempotency, which needs API support. |
| **Category mapping** | GoGov's service categories will not match this system's nine departments one-to-one. Someone has to sit down and map them, and keep them mapped. |
| **Two sources of truth** | If GoGov holds FAQs and the AI has its own knowledge base, they drift. One has to win. |
| **Support boundaries** | When a resident says "I filed a request and nothing happened," who diagnoses it — the Village, GoGov, or you? |

### What is genuinely possible

Order these from easiest to hardest — it shows you've thought about sequencing.

**1. Deep links — no API needed at all.** The assistant says *"I can text you a
link to the pothole report form"* and sends the existing GoGov URL. The resident
finishes on their phone, with a photo. Zero integration, works next week, and
solves the photo problem for free. **Lead with this.**

**2. Answering questions about GoGov.** "How do I report a pothole?" → "Through
the Village app or the Request for Service page, here's how." No API required —
just Village content.

**3. Structured email or webhook.** Many municipal platforms accept requests by
email into a queue. If GoGov does, the assistant can format and send one without
any API.

**4. Read-only FAQ sync.** If GoGov exposes FAQs, ingest them so answers stay
consistent with what residents see in the app.

**5. Full request creation.** The real integration — and the one that needs API
docs, credentials, a sandbox, and a decision about collecting contact details.

### The line to use

> "The honest sequence is: get the AI answering questions well first — that needs
> nothing from GoGov. Then link out to GoGov forms, which also needs nothing.
> Only after that does full request creation make sense, and that starts with
> you asking GoGov what API access is available on your contract. I'd rather
> deliver the first two than promise the third before anyone's confirmed it's
> possible."

### If they say "GoGov already does AI"

Several govtech vendors have added AI features. Don't be defensive:

> "That's worth looking at, and if it does what you need you should use it. The
> questions I'd ask them are: does it cite where each answer came from, what does
> it do when it doesn't know, and can your staff correct it directly? Those are
> the three things I focused on, because a confident wrong answer about a permit
> fee is the actual risk."

That reframes to your strengths without attacking anyone.

---

## Questions they will ask

### About accuracy and risk

**"What if it tells a resident something wrong?"**
> "That's the problem I spent most of the time on. It only answers when the
> retrieved Village information strongly supports it, and a second check verifies
> the answer against the source before it's spoken. If either fails, it says it
> doesn't have that detail and gives the department's number. Every answer cites
> the page it came from, so anything wrong is traceable to a page you can fix."

**"Can you prove it refuses?"**
> "Yes, and I'd encourage you to try to break it. Ask it something obscure. That
> demo is more convincing than any correct answer."

**"Who's liable if it's wrong?"**
> Don't improvise. "That's a real question and it's above my pay grade — it needs
> your counsel. What I can tell you is the system is built so it can't assert
> something that isn't in verified Village information, and every answer is
> traceable. How you want to handle disclaimers and liability is a Village
> decision I'd follow."

### About cost

**"What does it cost?"**
> "Depends where it runs. Roughly $40 a month on Village hardware — and that's
> almost entirely telephony, because the AI itself has no per-call cost when it
> runs locally. Fully managed in the cloud is $300–450. Commercial AI
> receptionist platforms are typically $0.10–0.25 a minute, which at your volume
> is $900–2,000+ a month."

**"What's the catch?"**
> "Staff time. Someone at the Village has to verify the information it can give
> out. That's 20–40 hours up front and an hour or two a week after. That's true
> of any vendor — it's just usually not mentioned."

### About people

**"Is this replacing staff?"**
> "No. It handles the repetitive calls — hours, schedules, where to apply — so
> your staff spend time on things that need judgment. It routes *to* people; it
> can't do their work. Where it helps most is after hours and at peak times,
> when the alternative is voicemail."

If there's a union, expect this to matter more than the technology. Don't
dismiss it.

**"Who maintains it?"**
> Answer honestly about your own availability. If you're a student or this is a
> side project, say so. "Right now it's me. For anything real, you'd want it
> either supported by a vendor or handed over with documentation so your IT can
> run it. I've written it to be handed over — there's a full setup guide and a
> test suite."

### About data

**"Where does resident data go?"**
> "Right now, nowhere — everything runs on one machine. No name, phone number,
> or address is stored anywhere in the system, and transcripts delete after seven
> days by default. If you moved to a cloud AI model that changes, and that's a
> decision you'd want your counsel involved in."

**"Are calls recorded?"**
> "Audio isn't stored. Text transcripts are, for seven days by default, and you
> can set that to whatever your records policy requires — or off. Whether those
> transcripts are public records under state retention law is a question for your
> Clerk, and worth asking early."

### About scope

**"Can it do Spanish?"**
> "The underlying models handle it. It would need Spanish content and real
> testing — a genuine piece of work, not a checkbox. Worth scoping if you have
> meaningful Spanish-speaking demand."

**"What about accessibility?"**
> "Important, and I'd flag it as an open item rather than claim it's handled.
> Callers using relay services or with atypical speech need a guaranteed path to
> a person. That's a requirement for any deployment."

**"Can it take payments?"**
> "No, and I'd argue it shouldn't. Payment card handling brings PCI compliance
> into scope. Better to send them to your existing payment page."

**"Can it look up my tax balance?"**
> "Not as built, deliberately. Account lookups are on a hard block list that
> always routes to a person, because it needs to know who's calling and that's a
> privacy question the Village should decide on, not something I'd default to."

### About the technology

**"What AI is it using?"**
> "An open-source model running locally, so nothing leaves the machine. It's
> built so that's swappable — if you'd rather use a commercial cloud model for
> speed, that's a configuration change, not a rebuild."

**"How long did this take?"**
> Be honest. Understating it makes them wonder what's missing.

**"How does it know about Garden City?"**
> "It read your website — about 90 pages, respecting your robots.txt and rate
> limits. It also picked up the sanitation schedule PDF. Everything it says comes
> from those documents, and it links back to them."

**"Did you scrape our site?"**
> Answer directly: *"I crawled the public pages your sitemap lists, one request
> per second, obeying your robots.txt, with an identifying user agent. Nothing
> behind a login, nothing your robots file disallows. Happy to stop or restrict
> it to a subset if you'd prefer."*

---

## Questions YOU should ask

Ask these. A call where you only answer looks like a sales pitch; a call where
you ask looks like engineering.

**The one that matters most:**
> **"Can your phone system forward a call to an external number or SIP address?"**

If yes, integration is hours. If no, it's a much bigger project. Everything about
telephony timeline hangs on this.

**Also ask:**
1. "What phone system do you use, and is it cloud or on-premise?"
2. "Who manages it — internal IT or an outside vendor?"
3. "Roughly how many calls a day, and what are the top five reasons people call?"
4. "What happens to calls after hours right now?"
5. "Is your website the authoritative source, or does staff know things that aren't published?"
6. "Are there policies about resident data leaving Village systems?"
7. "What does your GoGov contract actually include — do you know if API access is part of it?"
8. "Who would need to approve something like this?"
9. "Is there a budget cycle this would have to fit?"

Write down the answers. They shape everything you'd propose next.

---

## Things to be upfront about

Volunteering these makes everything else believable:

1. Not production-ready — no authentication, no encryption at rest, no audit trail
2. Content is unverified — read off your public site, nobody at the Village has confirmed it
3. Department phone numbers came from your online directory and should be re-checked
4. No phone number connected yet — browser only so far
5. One caller at a time in the current setup
6. The sanitation PDF on your site is labeled 2026-2027 but contains the 2024-2025 schedule

That last one is a gift. It shows you actually read their material, and it's a
real finding they can fix today regardless of what happens with this project.

---

## If the call goes well — what to propose

Don't propose a contract. Propose the smallest next step:

> "The most useful next step is probably 30 minutes with whoever knows your
> phone system and whoever owns the website content. I can show it working, and
> we'd know pretty quickly whether it's worth going further."

Low commitment, gets you in front of the right people, and the demo does the
selling.

## If they're lukewarm

Don't push. Ask what would make it useful:

> "That's fair. Is there a specific thing residents call about constantly that
> would be worth solving on its own? I'd rather do one thing well than propose
> something broad."

Often produces a better project than the one you walked in with.

---

## Tone

- **Slower than feels natural.** No visuals means they're building a picture from your words alone.
- **Stop after each answer.** Silence is them thinking, not disagreement.
- **"I don't know" is a strong answer** when followed by "I'll find out."
- **Don't oversell.** These are people responsible for public services. Measured beats enthusiastic.
- **Have the URL ready** in case they want to look while you talk.
