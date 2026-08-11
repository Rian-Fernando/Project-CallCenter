# Village of Garden City — AI Receptionist

A local-first proof of concept for an AI voice receptionist that answers
resident questions using **official Village of Garden City information**, routes
requests to the right department, and **refuses to guess** when it doesn't know.

> **This is a proof of concept, not a production municipal system.**
> It has no authentication, no encryption at rest, and no audit trail.
> See [SECURITY_ROADMAP.md](SECURITY_ROADMAP.md) before considering real callers.

**Runs entirely on your machine at $0.** No paid LLM, speech, telephony, or
vector-database services. No API keys. No accounts.

---

## The design principle

> The AI should be helpful, but it must never fabricate municipal information.

When the knowledge base doesn't contain an answer, the correct output is:

> *"I don't have enough verified information to answer that accurately.
> I can connect you with the appropriate department."*

That behavior is preferred over a plausible guess about a fee, deadline, or
policy — because a resident acting on a wrong municipal answer is a real harm.

---

## What it does

| | |
|---|---|
| **Answers from real Village content** | 88 pages crawled from gardencityny.net, every answer cited with a live URL |
| **Routes to 9 departments** | Deterministic rules first (instant, auditable), LLM only when ambiguous |
| **Refuses to guess** | Six independent confidence signals decide answer / clarify / escalate |
| **Remembers context** | *"I have a question about garbage pickup"* → *"When is mine?"* resolves correctly |
| **Escalates to humans** | Simulated transfer card with reason, transcript, and recommended action |
| **Learns from humans only** | Admins approve answers into the KB; the AI can never write to it |
| **Speaks and listens** | Whisper transcription, Kokoro speech, sentence-streaming, barge-in |
| **Records everything** | Full decision trace per turn, so "why did it refuse?" always has an answer |

---

## Architecture

```
                        RESIDENT
                           │
                  Browser (React, :5173)
                           │  mic → MediaRecorder
                           ▼
              ┌────────────────────────────┐
              │  FastAPI Orchestrator :8000│
              │                            │
              │  AudioIngress              │  ← browser | phone | SIP | WebRTC
              │      ▼                     │    all identical to the AI
              │  SpeechToText  (Whisper)   │
              │      ▼                     │
              │  ConversationMemory        │  ← session-scoped, no profiles
              │      ▼                     │
              │  IntentRouter              │  ← rules first, LLM on ambiguity
              │      ▼                     │
              │  RAG (LlamaIndex + Qdrant) │
              │      ▼                     │
              │  ConfidenceEngine          │  ← 6 signals → answer/clarify/escalate
              │      ▼                     │
              │  LLM  (Ollama, qwen3:8b)   │  ← grounded, cited
              │      ▼                     │
              │  TextToSpeech  (Kokoro)    │
              │      ▼                     │
              │  ConversationLogger        │  ← SQLite / PostgreSQL
              └────────────────────────────┘
```

Every external dependency sits behind an abstract base class in
`backend/app/providers/`. Swapping the local model for a hosted one, or the local
voice for a hosted one, means adding one file and changing one environment
variable — no call sites change. See [PRODUCTION_ROADMAP.md](PRODUCTION_ROADMAP.md).

---

## The confidence engine

This is the part that matters most.

**We never ask the model "how confident are you?"** Measured on this exact
stack, `qwen3:8b` reported `confidence: 1.0` while classifying a request it had
no grounds to be certain about. Self-assessment is not evidence.

Instead, six independent signals combine — five of which the model cannot
influence at all:

| Signal | Source | What it catches |
|---|---|---|
| Top retrieval score | Vector store | Is anything relevant at all? |
| Score margin (top1 − top3) | Vector store | Flat distribution = topic matched, fact didn't |
| Supporting document count | Corpus | Corroboration across sources |
| Department agreement | Cross-check | Router and retrieval disagree = one is wrong |
| **Grounding verification** | LLM critic | Is every claim actually in the excerpts? |
| Policy restrictions | `config/confidence.yaml` | Legal advice, account lookups, emergencies |

```
score ≥ 0.62  →  ANSWER    with citations
score ≥ 0.38  →  CLARIFY   ask one narrowing question
score <  0.38  →  ESCALATE  refuse, offer a human
```

A policy hit forces escalation regardless of score. A draft that *declines to
answer* also forces escalation — that's evidence the knowledge base lacks the
answer, not evidence of a good response.

Weights and thresholds live in `config/confidence.yaml` and `.env`, tunable by
Village staff without touching code. The full signal breakdown is returned in
every API response and rendered in the admin UI.

---

## Quick start

Full step-by-step instructions with troubleshooting: **[SETUP_GUIDE.md](SETUP_GUIDE.md)**

```bash
# 1. Install and start Ollama (~10 min, mostly model download)
brew install ollama ffmpeg
brew services start ollama
ollama pull qwen3:8b
ollama pull nomic-embed-text

# 2. Set up the project
./scripts/setup.sh

# 3. Load Village knowledge (~2.5 min, rate-limited crawl)
./scripts/crawl.sh
./scripts/ingest.sh

# 4. Run it
./scripts/dev.sh
```

Open **http://localhost:5173** and press **Start call**.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| macOS / Linux | Developed and verified on macOS 26 (Apple Silicon) |
| **16 GB RAM** | The 8B model needs ~5.2 GB resident |
| Python 3.12 | 3.13 also works; 3.11 minimum |
| Node.js 20+ | Verified on 24 |
| ~7 GB disk | Models, dependencies, index |
| Homebrew | For Ollama and ffmpeg |

**Not required:** Docker, PostgreSQL, a Qdrant server, Asterisk, or any API key.

---

## Model choice

`qwen3:8b` (Q4_K_M, 5.2 GB) with **thinking mode explicitly disabled**.

Benchmarked on an Apple M2 Pro (16 GB):

| | Routing latency | Result |
|---|---|---|
| `think: true` | **9.93 s** | correct |
| `think: false` | **1.29 s** | identical |

The reasoning phase burned 1,050 characters of internal monologue to reach the
same conclusion — fatal for a voice interface. `OLLAMA_THINKING=false` is the
default.

**Lower-spec machines:** set `OLLAMA_MODEL=llama3.2:3b` (~2 GB) for roughly 2×
faster responses at some quality cost.

Embeddings use `nomic-embed-text` served by Ollama, and speech uses
faster-whisper (CTranslate2) and Kokoro (onnxruntime). **PyTorch is never
installed** — the Python environment is ~800 MB instead of ~3.5 GB.

### Faster responses

Answers are spoken **sentence by sentence as they generate** rather than after
the full answer completes, which cuts time-to-first-audio from ~5s to ~1.5s.

If you want more speed and accept that resident text leaves the machine, the
Gemini free tier is implemented and one env var away — but read the privacy
warning in `.env` first. It is **not** appropriate for real resident calls on
the unpaid tier.

---

## Project layout

```
backend/
  app/
    api/         health, chat, voice, admin endpoints
    core/        config, logging, error handling
    models/      SQLAlchemy tables + Pydantic schemas
    providers/   ← the vendor-swappable seam
      llm/         ollama_provider.py + documented stubs
      stt/         whisper_provider.py
      tts/         kokoro → piper → macOS say → browser fallback chain
      embeddings/  ollama_embeddings.py
      vectorstore/ qdrant_provider.py
    rag/         crawler, cleaning, chunking, ingestion, retrieval
    routing/     department registry + intent router
    services/    confidence, conversation, memory, escalation, retention
    integrations/gogov.py (mock)
  tests/         117 tests
frontend/src/
  pages/         Receptionist, Demo, Dashboard, Review, Privacy
  components/    CallOrb, TranscriptPanel, shared UI
  hooks/         useMicrophone (VAD), useSpeech (barge-in)
knowledge/       department folders + crawl cache
config/          departments.yaml, confidence.yaml
scripts/         setup, crawl, ingest, dev, test
docker/          OPTIONAL Qdrant + Postgres
```

---

## API

Interactive documentation at **http://127.0.0.1:8000/docs**.

```
POST   /api/chat                       Full conversational turn
POST   /api/chat/stream                Same, streamed over SSE
POST   /api/voice/transcribe           Audio → text
POST   /api/voice/synthesize           Text → audio/wav
POST   /api/voice/turn                 Audio → complete turn
POST   /api/rag/search                 Retrieval only
POST   /api/routing/classify           Department classification only
GET    /api/departments
GET    /api/health                     Per-provider status + fix commands
GET    /api/analytics                  Dashboard metrics from the database
GET    /api/conversations              Logged calls
DELETE /api/conversations/{id}         Permanent deletion
GET    /api/unanswered                 Human review queue
POST   /api/knowledge/review/{id}      Claim / annotate an item
POST   /api/knowledge/approve          ← the only path knowledge enters
GET    /api/privacy/settings           Retention configuration
POST   /api/privacy/purge              Delete past the retention window
```

---

## Knowledge base

Ingested from `gardencityny.net` by a **polite crawler**: obeys `robots.txt`,
one request at a time at 1 req/sec, identifies itself, visits only sitemap URLs,
and caches to disk so it never re-fetches. Please don't lower
`CRAWL_DELAY_SECONDS`.

Documents carry an `is_official` flag. Anything false is labeled
**DEMO DATA — NOT OFFICIAL VILLAGE INFORMATION** everywhere it appears, in the
UI and in the model's own context. Local files default to `false`; that default
is deliberate.

Add knowledge two ways:
- **Admin UI** (`/admin/review`) — answer a queued question and approve it. Indexed immediately.
- **Files** — drop into `knowledge/<department>/` and run `./scripts/ingest.sh`.

See [knowledge/README.md](knowledge/README.md) for formats.

---

## Testing

```bash
./scripts/test.sh                      # everything (117 tests)
./scripts/test.sh -m "not integration" # fast; no Ollama needed
```

Integration tests **skip automatically** when Ollama or the knowledge base is
unavailable, so a fresh checkout is never red for environmental reasons.

Coverage includes routing (all four specified cases plus disambiguation),
safety (policy overrides, refusal, grounding failure modes), RAG (retrieval
correctness, citation retention, cleaning), and conversation memory.

---

## Demo scenarios

Open **http://localhost:5173/demo** — five one-click scenarios, no microphone
required:

1. *"Where do I report a pothole?"* → routes to Public Works
2. *"When is garbage collection?"* → cited answer from the Village knowledge base
3. *"I need a building permit"* → routes to the Building Department
4. A question outside the knowledge base → declines and offers a transfer
5. *"I have a question about garbage pickup"* → *"When is mine?"* → context resolved

---

## Telephony

| | Cost | Status |
|---|---|---|
| **A. Browser demo** | $0 | ✅ built |
| **B. Local Asterisk** | $0 software | documented, optional — [docs/ASTERISK.md](docs/ASTERISK.md) |
| **C. Real phone number** | Requires a SIP/telephony provider | documented path |

A real phone number cannot be free. The AI backend doesn't care where audio
comes from — `AudioIngress` treats browser, phone, SIP, and WebRTC identically,
so option C is a front-end change, not a rewrite.

---

## Privacy

- **No personal information is stored.** No name, phone number, address, or account reference exists in the schema.
- Session IDs are random per call and never linked to a person.
- Audio is processed in memory; `STORE_AUDIO=false` by default.
- Transcripts live in `data/gardencity.db` with a **7-day default retention**.
- `/admin/privacy` provides retention selection and real deletion.

---

## Troubleshooting

Check **http://127.0.0.1:8000/api/health** first — it reports each provider's
state and the exact command that fixes it.

| Symptom | Fix |
|---|---|
| `llm: unavailable` | `brew services start ollama` |
| `Model not installed` | `ollama pull qwen3:8b` |
| `vector_store: degraded, 0 chunks` | `./scripts/crawl.sh && ./scripts/ingest.sh` |
| Ingestion says the server holds a lock | Stop the API first, or use Qdrant server mode |
| Slow first response | Cold model load; `OLLAMA_KEEP_ALIVE=30m` keeps it warm |
| Microphone blocked | Allow it in browser settings; typing works regardless |
| Port 5173 in use | Change `server.port` in `frontend/vite.config.ts` |

More detail in [SETUP_GUIDE.md](SETUP_GUIDE.md#troubleshooting).

---

## Documentation

| Document | Contents |
|---|---|
| [SETUP_GUIDE.md](SETUP_GUIDE.md) | 14 numbered steps, each with what to run, what you should see, and what to do if it fails |
| [PRODUCTION_ROADMAP.md](PRODUCTION_ROADMAP.md) | Free → production migration, component by component, with costs |
| [SECURITY_ROADMAP.md](SECURITY_ROADMAP.md) | Everything required before real municipal callers |
| [docs/GOGOV_INTEGRATION.md](docs/GOGOV_INTEGRATION.md) | What GoGov would need to provide |
| [docs/ASTERISK.md](docs/ASTERISK.md) | Optional telephony path |

---

## License and status

Proof of concept, built for evaluation by the Village of Garden City.
Not an official Village service. Village content is reproduced from public web
pages for demonstration and remains the Village's.
