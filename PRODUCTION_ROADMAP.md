# Production Roadmap

How the free local prototype maps to a system that could serve real Garden City
residents — component by component, with honest costs.

> Read [SECURITY_ROADMAP.md](SECURITY_ROADMAP.md) alongside this. Nothing here
> should go live without that work done first.

---

## The migration table

| Component | Free prototype | Production option | Can stay open-source? |
|---|---|---|---|
| LLM | Ollama · `qwen3:8b` | Gemini / GPT / other hosted | ✅ self-host vLLM on a GPU VM |
| Embeddings | Ollama · `nomic-embed-text` | Voyage / OpenAI / Cohere | ✅ same model, self-hosted |
| Speech-to-text | faster-whisper `base.en` | Deepgram / AssemblyAI / Azure Speech | ✅ self-host `whisper-large-v3` |
| Text-to-speech | Piper | ElevenLabs / Azure / Polly | ✅ Piper scales fine |
| Vector DB | Qdrant embedded | Qdrant Cloud / Pinecone / pgvector | ✅ Qdrant self-hosted |
| Database | SQLite | Azure Database for PostgreSQL | ✅ PostgreSQL anywhere |
| Telephony | Browser WebRTC | Twilio / SIP trunk / Asterisk | ✅ Asterisk or FreeSWITCH |
| Voice orchestration | Custom (this repo) | Retell / Vapi | ✅ this repo is the alternative |
| API hosting | Uvicorn on localhost | Azure Container Apps / Fly / Render | ✅ any container host |
| Frontend | Vite dev server | Azure Static Web Apps / Vercel | ✅ any static host |
| Monitoring | stdout logs | Azure Monitor / Datadog / Grafana | ✅ Prometheus + Grafana + Loki |
| Secrets | `.env` | Azure Key Vault / AWS Secrets Manager | ✅ HashiCorp Vault |

**The Village could run a fully open-source production stack.** Nothing here
requires a commercial vendor. The trade is operational burden versus licence
cost.

---

## Why the migration is cheap in engineering terms

Every external dependency sits behind an abstract base class in
`backend/app/providers/base.py`. Application code imports only those
interfaces — it never imports a vendor SDK.

Swapping the LLM to a hosted provider (Gemini is already implemented):

1. Set `LLM_PROVIDER=gemini` and `GEMINI_API_KEY=…` in `.env`
2. For a different vendor, implement `complete()` and `stream()` in `providers/llm/future_providers.py` (the stub documents exactly what to map)
3. Register it in `providers/factory.py::_LLM_PROVIDERS`

**No other file changes.** Routing, RAG, confidence scoring, conversation
memory, and the API contract are all untouched. The same shape applies to STT,
TTS, embeddings, and the vector store.

---

## Deployment options, honestly costed

### Option A — Stay local (recommended for evaluation)

**$0/month.** Backend on a Village workstation, browser demo on the same
machine. Correct for demonstrating the concept to Village leadership. This is
what the repository currently is.

### Option B — Frontend hosted, backend tunneled

**$0/month.** Deploy `frontend/` to Vercel or Netlify (static build, free
tier). Expose the local backend with `cloudflared tunnel --url
http://localhost:8000` and set `VITE_API_BASE_URL` to the tunnel URL.

Good for remote demos. The host machine must stay awake, and this is **not**
suitable for real residents — no authentication, no availability guarantee.

> **Vercel cannot host this backend.** Ollama needs a 5.2 GB model resident in
> RAM and 5–10s of inference per turn; serverless functions have a ~250 MB
> bundle cap, a 10–60s timeout, no persistent process, and an ephemeral
> filesystem. This is a platform mismatch, not a configuration problem.

### Option C — Self-hosted containers, open-source stack

**~$70–250/month.** A GPU VM (or a large CPU VM with a smaller model) running
Ollama or vLLM, plus containers for the API, Qdrant, and PostgreSQL.

Keeps all data on infrastructure the Village controls — often the deciding
factor for a municipality. Requires someone to operate it.

### Option D — Managed services

**~$150–600/month** at low volume, plus per-minute telephony.

| Line item | Rough cost |
|---|---|
| LLM API | $0.30–3.00 per 1,000 turns depending on model |
| Managed STT | ~$0.006/minute |
| Managed TTS | ~$0.015/1,000 characters |
| Telephony | ~$0.013/minute inbound + ~$1–2/month per number |
| Managed Postgres | ~$25–75/month |
| Managed vector DB | ~$25–70/month |
| Container hosting | ~$30–100/month |

Lowest operational burden, highest recurring cost, and resident transcripts
flow through third parties — which drives the vendor-agreement and data
residency work in the security roadmap.

---

## Phased plan

### Phase 1 — Harden the prototype (2–3 weeks)

- Authentication on every `/api/admin/*` route (see security roadmap)
- Alembic migrations instead of `create_all`
- Structured JSON logging with correlation IDs
- Rate limiting per session and per IP
- Health checks wired to real readiness/liveness probes
- Container images for API and frontend
- CI running the existing 117 tests

### Phase 2 — Validate the knowledge base (2–4 weeks, mostly Village staff time)

**This is the highest-value phase and the least technical.**

- Village staff review every ingested document for accuracy
- Populate `phone` and `email` in `config/departments.yaml` from the official directory — **currently deliberately null so nothing is invented**
- Replace all `DEMO DATA` placeholders with verified content
- Build an evaluation set of ~100 real resident questions with correct answers
- Measure retrieval accuracy and refusal rate against it, then tune thresholds
- Establish who owns knowledge updates and how often they run

Skipping this is the most likely way for a technically sound system to fail in
production.

### Phase 3 — Production infrastructure (3–4 weeks)

- Migrate to managed PostgreSQL with automated backups and tested restores
- Qdrant in server mode with persistent volumes and snapshots
- Blue/green or rolling deploys
- Monitoring and alerting: latency, error rate, escalation rate, refusal rate
- Load testing at expected call volume
- A documented incident runbook

### Phase 4 — Telephony (2–3 weeks)

- Provision a SIP trunk or Twilio number
- Bridge audio into the existing `AudioIngress` abstraction — the AI layer needs no changes
- Implement real transfers to department extensions, replacing the simulated card
- Add DTMF fallback ("press 1 for…") for callers who prefer it
- Test on real handsets across cellular and landline

### Phase 5 — Pilot (4–8 weeks)

- Route a **single low-risk department** to the AI — sanitation schedules are ideal: high volume, low stakes, factual answers
- Keep a human transfer path available at all times
- Publish that callers are speaking with an automated system
- Weekly review of the unanswered-questions queue
- Expand department by department only after each meets an accuracy bar

---

## What must change in the code

| Area | Prototype behavior | Production requirement |
|---|---|---|
| Auth | None | OIDC/SAML on admin routes, RBAC |
| Sessions | In-process dict | Redis, so instances can scale horizontally |
| Migrations | `create_all` | Alembic, reviewed and reversible |
| Rate limiting | None | Per-IP and per-session |
| Config | `.env` | Managed secret store |
| Errors | Logged to stdout | Aggregated with alerting |
| Retention | Manual purge endpoint | Scheduled job with audit records |
| Knowledge updates | Manual re-crawl | Scheduled with change detection and staff review |
| Model updates | Manual pull | Pinned versions, evaluated before promotion |

---

## What should NOT change

These are load-bearing design decisions, not prototype shortcuts:

1. **Confidence never comes from model self-assessment.** Retrieval statistics and an independent grounding critic are what make refusal trustworthy.
2. **Refusal is the default on uncertainty.** A wrong municipal answer costs more than a transfer.
3. **The AI cannot write its own knowledge.** Every entry passes through human approval.
4. **Every answer carries citations.** Unsourced municipal claims are not acceptable.
5. **`is_official` is respected everywhere.** Unverified content is always labeled.
6. **Emergencies always route to 911.** Never handled by the assistant.
7. **No personal information is stored** unless a specific, documented need arises.
8. **Rules run before the LLM in routing.** Cheaper, faster, and explainable to staff.

---

## Scaling notes

**Current single-instance capacity:** roughly 1 concurrent call. Ollama
serializes inference, so a second caller queues behind the first.

To handle real volume:

- **Horizontal:** multiple API instances behind a load balancer, sessions in Redis, a shared Qdrant server. The stateless design already supports this — only `SessionStore` needs replacing.
- **Inference:** vLLM with continuous batching handles many concurrent requests far better than Ollama, which is built for single-user local use.
- **Cost control:** the deterministic router already avoids an LLM call on most turns. Prompt caching on the static system prompt cuts hosted-LLM cost substantially, since it is identical on every call.

**Realistic target:** a Village the size of Garden City might see 50–200 calls
per day. A single well-provisioned instance handles that comfortably; the
constraint is concurrency at peak, not daily volume.
