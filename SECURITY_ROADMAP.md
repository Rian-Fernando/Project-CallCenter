# Security Roadmap

What would have to change before this system could handle real calls from
Garden City residents.

---

## Current status: NOT production-ready

Stated plainly, because a municipality deploying this as-is would be exposed.

**The prototype has no authentication, no encryption at rest, no audit trail,
and no rate limiting.** It is safe only because it binds to `127.0.0.1`, holds
no real resident data, and is operated by the developer who built it.

Do not expose this to the internet in its current state.

### What it does get right

Worth noting, because these are architectural rather than bolt-on:

- **No personal information is collected.** No name, phone number, address, or account reference exists anywhere in the schema.
- **Session IDs are random UUIDs**, generated per call, never linked to a person, and discarded on expiry.
- **No caller profiles.** Memory is scoped to a single conversation, by design (§12).
- **Audio is not persisted** (`STORE_AUDIO=false`).
- **Short default retention** — 7 days, with real deletion (rows removed, not flagged).
- **No third-party data flow.** Every model runs locally; no resident text leaves the machine.
- **Emergency routing to 911** is a hard, unconditional policy override.
- **Secrets are gitignored** — `.env` is excluded and `.env.example` contains no values.

---

## 1. Authentication and authorization

**Now:** every endpoint is open, including `DELETE /api/conversations` and
`POST /api/knowledge/approve`.

**Required:**

- OIDC/SAML against the Village identity provider for all admin routes
- Role separation:
  - *Viewer* — analytics only
  - *Reviewer* — answer and approve knowledge entries
  - *Administrator* — deletion, retention, configuration
- Session timeout and re-authentication for destructive actions
- MFA for administrators
- Resident-facing endpoints stay unauthenticated (residents don't log in) but must be rate-limited and abuse-monitored

**Priority: highest.** Nothing else matters while `/api/knowledge/approve` is
reachable by anyone who can hit the port — it writes directly into what the
assistant tells residents.

---

## 2. Transport and storage encryption

**Now:** plain HTTP on localhost; SQLite unencrypted on disk.

**Required:**

- TLS 1.3 everywhere, HSTS, no mixed content
- Encryption at rest for the database (managed Postgres encryption or full-disk)
- Encrypted backups with separately managed keys
- Encrypted vector storage — retrieved passages can quote resident-specific content once staff-authored answers are added

---

## 3. Secrets management

**Now:** `.env` on disk. Fine locally; unacceptable in production.

**Required:**

- Azure Key Vault, AWS Secrets Manager, or HashiCorp Vault
- Injected at runtime, never baked into images
- Rotation policy and a documented rotation runbook
- CI secret scanning (`gitleaks` or equivalent) to catch accidental commits
- Distinct credentials per environment

---

## 4. Logging and audit trail

**Now:** developer logs to stdout. Transcripts are logged only at DEBUG, so a
normal INFO run records no conversation content — a deliberate choice worth
keeping.

**Required:**

- Structured JSON logs with correlation IDs, shipped to a retained store
- **An immutable audit trail** for every privileged action: who approved which knowledge entry, who deleted which conversation, who changed retention, when, and from where. For a municipality this is likely a records-retention obligation, not just good practice.
- Explicit guarantee that PII never enters application logs
- Log retention aligned with the Village's records schedule — which may be **longer** than transcript retention

---

## 5. PII and transcript handling

**Now:** the schema stores no PII, but **residents may still speak it.** Someone
will say their address or account number aloud, and it lands in `turns.user_text`.

**Required:**

- PII detection and redaction on transcripts before storage (Presidio or equivalent) — addresses, phone numbers, account numbers, names
- A documented decision on whether transcripts are records under NY State retention law, made with the Village Clerk and counsel
- A resident-facing privacy notice, and spoken disclosure that the call is automated and may be recorded
- A defined process for resident data access and deletion requests
- Confirm whether any transcript content constitutes a FOIL-responsive record

**This is the item most likely to be underestimated.** The schema being clean
does not mean the data is.

---

## 6. Access control on knowledge

**Now:** anyone reaching the API can approve a knowledge entry, and it is
immediately spoken to residents as Village information.

**Required:**

- Approval restricted to authenticated reviewers
- Two-person review for anything marked `is_official: true`
- Full version history with the ability to roll back an entry
- Periodic re-verification (municipal facts go stale — fees change, schedules change)
- A clear owner per department for knowledge accuracy

---

## 7. Rate limiting and abuse prevention

**Now:** none. A loop could exhaust CPU or fill the review queue with garbage.

**Required:**

- Per-IP and per-session rate limits on chat, transcription, and synthesis
- Upload size caps (partially present: `MAX_AUDIO_BYTES` is 25 MB)
- Cost ceilings once a metered LLM is in use
- Bot detection on the public endpoint
- Alerting on unusual escalation-rate or volume spikes

---

## 8. Prompt injection and content safety

**Now:** the system prompt instructs the model to answer only from excerpts, and
the grounding critic verifies that afterwards. That is meaningful protection but
not a guarantee.

**Required:**

- Treat ingested web content as untrusted input. A crawled page containing *"ignore previous instructions"* is a real vector once the Village site accepts user-generated content.
- Output filtering before speech synthesis
- Cap conversation length to limit context manipulation
- Red-team the refusal behavior specifically: try to talk it into stating a fee, a deadline, or a legal interpretation it has no source for
- Monitor for answers whose grounding verdict is `unsupported` reaching residents

---

## 9. Availability and incident response

**Required:**

- Documented SLO (e.g. 99.5% during business hours)
- Health checks driving automatic restarts
- **A fallback path when the AI is unavailable** — calls must reach a human, never a dead line
- On-call rotation and escalation contacts
- Written incident runbook: how to disable the AI and revert to human answering within minutes
- A defined breach notification process aligned with NY State law
- Post-incident review process

---

## 10. Backups and recovery

**Required:**

- Automated daily database backups, encrypted, retained per Village policy
- **Restore drills** — an untested backup is not a backup
- Vector index snapshots (or documented, timed rebuild-from-source)
- `config/*.yaml` and knowledge entries under version control
- Stated RPO/RTO

---

## 11. Data residency and vendor agreements

**Currently a non-issue** — everything is local, no data leaves the machine.
**This changes the moment a hosted model is adopted.**

**Required before any hosted API:**

- Confirm processing region (US, ideally East Coast for latency)
- Signed DPA with every processor
- Written confirmation that resident data is **not used for model training**
- Documented sub-processor list
- Vendor security review (SOC 2 Type II or equivalent)
- Contractual deletion guarantees and exit terms
- Legal review of whether resident transcripts may leave Village control at all

This deserves a decision early: it may rule out managed LLMs entirely and push
the Village toward the self-hosted path in the production roadmap.

---

## 12. Monitoring

**Required:**

- Latency (p50/p95/p99) per pipeline stage — the timing breakdown is already captured per turn
- Error rate by type
- **Escalation rate** — a spike means the knowledge base has drifted
- **Refusal rate** — a drop may mean the system became overconfident
- Grounding-verdict distribution — rising `unsupported` is an early hallucination warning
- Resource use, and cost per call once metered
- Alerting with defined thresholds and owners

---

## Pre-launch checklist

Nothing goes live until every box is ticked.

**Blocking**

- [ ] Authentication on all admin endpoints
- [ ] TLS everywhere
- [ ] Encryption at rest
- [ ] Secrets in a managed store
- [ ] Audit trail for privileged actions
- [ ] Rate limiting
- [ ] PII redaction on transcripts
- [ ] Legal review of transcript retention under NY State law
- [ ] Tested backup restore
- [ ] Human fallback when the AI is down
- [ ] Incident runbook with an AI kill switch
- [ ] Village staff have verified every knowledge document
- [ ] Department phone numbers populated from the official directory
- [ ] Spoken disclosure that the caller is speaking with an automated system

**Strongly recommended**

- [ ] Red-team exercise against fabrication and prompt injection
- [ ] Load test at expected peak concurrency
- [ ] Evaluation set of real resident questions with measured accuracy
- [ ] Accessibility review (callers with speech differences, non-English speakers)
- [ ] Pilot limited to one low-risk department first

---

## A note on scope

The hardest problems here are not technical. Deciding whether resident call
transcripts are public records, what happens when the AI gives a wrong answer
about a permit fee, and who is accountable for knowledge accuracy are
governance questions.

The architecture supports the right answers — refusal over guessing, citations
on every claim, human-gated knowledge, no PII collection, real deletion. But
those are properties of the system, not a substitute for the Village deciding
how it wants to use it.
