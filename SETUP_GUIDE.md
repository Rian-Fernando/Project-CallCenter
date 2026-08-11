# Setup Guide

Written for a developer who may not have set up local AI infrastructure before.
Every step tells you **what to do**, **what it does**, **what you should see**,
and **what to do if it fails**.

Total time: ~20 minutes, most of it downloading a 5 GB model.

> **A note on this project's path:** the repository sits inside directories
> containing `~` and spaces (`~JAR_VIS~/~AI PROJECT~`). Everything here handles
> that correctly, but if you write your own shell commands, **quote your paths**.

---

## Step 0 — Check your machine

**What you do**

```bash
sw_vers ; uname -m ; sysctl -n hw.memsize | awk '{print $1/1073741824" GB RAM"}'
python3 --version ; node --version ; df -h / | tail -1
```

**What this does** — confirms you have the hardware and runtimes for a local 8B model.

**What you should see**

- macOS 13+ (or a recent Linux), `arm64` or `x86_64`
- **16 GB RAM or more** ← the binding constraint
- Python **3.11+** (3.12 preferred)
- Node **20+**
- **8 GB free disk**

**If it fails**

- *Under 16 GB RAM* — you can still run this. In Step 3 pull `llama3.2:3b` instead and set `OLLAMA_MODEL=llama3.2:3b` in `.env`.
- *No Python 3.12* — `brew install python@3.12`
- *No Node* — `brew install node`

---

## Step 1 — Install Ollama and ffmpeg

**What you do**

```bash
brew install ollama ffmpeg
```

**What this does** — Ollama runs the language model locally. ffmpeg decodes
browser audio (Whisper bundles its own decoder, but system ffmpeg removes a
class of edge-case failures).

**What you should see** — both install; `ollama --version` prints a version.

**If it fails**

- *`brew: command not found`* — install Homebrew from https://brew.sh
- *Not on macOS* — use the Ollama Linux installer at https://ollama.com/download and `apt install ffmpeg`

---

## Step 2 — Start Ollama

**What you do**

```bash
brew services start ollama
```

**What this does** — runs Ollama as a background service on port 11434, so it
survives reboots and terminal closes.

**What you should see**

```bash
curl -s http://localhost:11434/api/tags
# {"models":[]}
```

An empty model list is correct at this point.

**If it fails**

- *Connection refused* — wait 5 seconds and retry; or run `ollama serve` in a separate terminal to see errors directly.
- *Port 11434 in use* — Ollama is probably already running. That's fine.

---

## Step 3 — Download the models

**What you do**

```bash
ollama pull qwen3:8b          # ~5.2 GB — the slow one
ollama pull nomic-embed-text  # ~274 MB
```

**What this does** — `qwen3:8b` generates answers and handles ambiguous
routing. `nomic-embed-text` turns text into vectors for search.

**Why this model** — benchmarked on an M2 Pro, `qwen3:8b` answers in ~1.2s with
thinking disabled, follows "answer only from these excerpts" reliably, and
fits in 16 GB alongside a browser. Larger models swap and make voice unusable.

**What you should see**

```bash
ollama list
# NAME                       SIZE
# qwen3:8b                   5.2 GB
# nomic-embed-text:latest    274 MB
```

**If it fails**

- *Download stalls* — `Ctrl-C` and re-run; Ollama resumes.
- *Out of disk* — free space, or use `ollama pull llama3.2:3b` (~2 GB) and set `OLLAMA_MODEL=llama3.2:3b` in `.env`.

---

## Step 4 — Set up the project

**What you do**

```bash
cd "path/to/CallCenter"
./scripts/setup.sh
```

**What this does** — creates a Python 3.12 virtualenv, installs backend
dependencies, copies `.env.example` to `.env`, and installs frontend packages.

**What you should see** — `Setup complete.` after a minute or two.

Note: **PyTorch is never installed.** faster-whisper uses CTranslate2 and Piper
uses onnxruntime, keeping the environment at ~800 MB.

**If it fails**

- *`No module named venv`* — `brew install python@3.12`
- *A specific package fails to build* — check your Python is 3.11–3.13: `./backend/.venv/bin/python --version`
- *npm errors* — `cd frontend && rm -rf node_modules package-lock.json && npm install`

---

## Step 5 — (Optional) Review configuration

**What you do**

```bash
open .env    # or: nano .env
```

**What this does** — shows every setting. Defaults are correct for local use;
you don't need to change anything.

Worth knowing:

| Setting | Default | Meaning |
|---|---|---|
| `OLLAMA_MODEL` | `qwen3:8b` | Change to `llama3.2:3b` for speed |
| `OLLAMA_THINKING` | `false` | Leave off — 8× slower for no gain |
| `QDRANT_URL` | *(blank)* | Blank = embedded, no Docker |
| `DATABASE_URL` | SQLite | One line to switch to Postgres |
| `RETENTION_DAYS` | `7` | Conversation retention |
| `CRAWL_DELAY_SECONDS` | `1.0` | **Please don't lower this** |

`.env` is gitignored and must never be committed.

---

## Step 6 — Crawl official Village content

**What you do**

```bash
./scripts/crawl.sh
```

**What this does** — fetches public pages from `gardencityny.net` listed in the
Village sitemap. It obeys `robots.txt`, makes **one request per second**, sends
an identifying User-Agent, and caches everything to `knowledge/_crawled/` so it
never re-fetches.

Takes about **2.5 minutes** — that's the rate limit, deliberately.

**What you should see**

```
Sitemap: 305 URLs | 0 blocked by robots.txt | crawling top 150 at 1.0s intervals
  ... 150/150 fetched (128 usable)
CRAWL COMPLETE — 128 pages, 1,169,725 characters
```

**If it fails**

- *No pages retrieved* — check internet access: `curl -I https://www.gardencityny.net`
- *Want to skip the crawl* — you can. `./scripts/ingest.sh` will index the placeholder files in `knowledge/`, but the demo is far weaker without real content.
- *Re-run later* — cached pages are reused; add `--refresh` to force re-fetching.

---

## Step 7 — Build the search index

**What you do**

```bash
./scripts/ingest.sh
```

**What this does** — loads crawled pages and local files, removes boilerplate,
splits them into ~400-token passages, embeds each with `nomic-embed-text`, and
stores the vectors in Qdrant.

**What you should see**

```
INGESTION COMPLETE
  Documents indexed : 91
    official        : 88
    demo/placeholder: 3
  Chunks embedded   : 183
```

`official: 88` means real Village content. `demo: 3` are clearly labeled
placeholders.

**If it fails**

- *"The API server is running and holds the embedded Qdrant lock"* — expected. Embedded Qdrant allows one process at a time:
  ```bash
  pkill -f 'uvicorn app.main:app'
  ./scripts/ingest.sh
  ```
  To avoid this permanently, use Qdrant in server mode (Step 13).
- *"Embedding model unavailable"* — `ollama pull nomic-embed-text`
- *0 documents* — run Step 6 first.

---

## Step 8 — Start the application

**What you do**

```bash
./scripts/dev.sh
```

**What this does** — starts FastAPI on `:8000` and the React dev server on
`:5173`. `Ctrl-C` stops both.

**What you should see**

```
  [OK  ] llm           Ollama ready with qwen3:8b
  [OK  ] embedding     nomic-embed-text ready (768 dims)
  [OK  ] vector_store  183 chunks indexed (embedded mode)
  [WARN] stt           Whisper 'base.en' will load on first use
  [WARN] tts           Voice 'en_US-lessac-medium' not downloaded yet
```

Those two `WARN` lines are normal — Whisper and Piper download on first use.

**If it fails**

- *Port 8000 in use* — `lsof -ti:8000 | xargs kill`
- *Port 5173 in use* — edit `server.port` in `frontend/vite.config.ts`
- *Frontend won't start* — `cd frontend && npm install`

---

## Step 9 — Verify system health

**What you do**

```bash
curl -s http://127.0.0.1:8000/api/health | python3 -m json.tool
```

**What this does** — probes every provider and, for each failure, returns the
exact command that fixes it. **This is your first stop whenever anything
misbehaves.**

**What you should see** — `"ready_for_calls": true`, with `llm`, `embedding`,
and `vector_store` all `"ok"`.

**If it fails** — read the `hint` field. It tells you what to run.

---

## Step 10 — Test retrieval

**What you do**

```bash
curl -s -X POST http://127.0.0.1:8000/api/rag/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"When is garbage collected?","top_k":3}' | python3 -m json.tool
```

**What this does** — searches the knowledge base without generating an answer.

**What you should see** — `top_score` above 0.7 and results titled *Sanitation*
or *Recycling*, each with `"is_official": true` and a `gardencityny.net` URL.

**If it fails**

- *Empty results* — the index is empty; re-run Step 7.
- *Low scores everywhere* — the crawl may have failed; check `ls knowledge/_crawled | wc -l` (expect ~128).

---

## Step 11 — Test department routing

**What you do**

```bash
for q in "Where do I report a pothole?" "I need a building permit" \
         "How do I pay my water bill?" "When is garbage day?"; do
  curl -s -X POST http://127.0.0.1:8000/api/routing/classify \
    -H 'Content-Type: application/json' -d "{\"text\":\"$q\",\"use_llm\":false}" \
    | python3 -c "import json,sys;d=json.load(sys.stdin);print(f\"{d['department']:14s} {d['confidence']}\")"
done
```

**What you should see**

```
public_works   0.76
building       0.9
finance        0.9
sanitation     0.9
```

**If it fails** — check `config/departments.yaml` loads: the server log should
say `Loaded 9 departments`.

---

## Step 12 — Test the browser voice demo

**What you do**

1. Open **http://localhost:5173**
2. Click **Start call**
3. **Allow microphone access** when the browser asks
4. Say: *"Hi, I have a question about garbage collection."*
5. Stop talking and wait

**What this does** — records until ~1.2s of silence, transcribes with Whisper,
streams the answer, and speaks it with Piper.

**What you should see**

- The orb shows a live waveform while you speak
- Your words appear in the transcript
- A department chip lights up (`Sanitation`)
- The answer streams in and is spoken aloud
- Source cards appear with clickable Village URLs

Then try a **follow-up**: *"When is mine?"* — it should stay on garbage
collection, proving conversation memory.

Then **interrupt it** mid-sentence by pressing **Speak** — audio cuts off.

**If it fails**

- *Microphone blocked* — allow it in browser settings and reload. **You can always type instead** — the text box below the orb runs the identical pipeline.
- *First response is very slow (~10s)* — expected once: Whisper (~145 MB) and the Piper voice (~65 MB) download on first use. Subsequent turns are ~3s.
- *No audio plays* — check the 🔊 toggle. If Piper failed, the backend falls back to macOS `say`, then to browser speech; the demo still speaks.
- *Safari issues* — Chrome is better supported for `MediaRecorder`.

---

## Step 13 — Test escalation and the review queue

**What you do**

1. Ask something the Village site doesn't cover: *"What is the airspeed velocity of an unladen swallow?"*
2. Open **http://localhost:5173/admin/review**

**What you should see**

- The assistant **declines to answer** and offers a transfer
- A **TRANSFER TO …** card with reason, transcript, and recommended action
- The question appears in the review queue with the confidence signals that failed

Now close the loop:

3. Click the question, write an answer, choose a department, click **Approve & add to knowledge base**
4. Return to the receptionist and ask the same question again — it now answers, citing your approved entry

That is the human-in-the-loop learning cycle. **The AI can never do this itself.**

**If it fails**

- *Approve fails to index* — Ollama must be running; the message will say so.

---

## Step 14 — Run the tests

**What you do**

```bash
./scripts/test.sh
```

**What you should see** — `117 passed`.

Integration tests skip automatically if Ollama or the index is unavailable, so
this is never red for environmental reasons.

---

## Optional — Qdrant in server mode

Removes the stop-the-server-to-ingest dance.

```bash
docker compose -f docker/docker-compose.yml up -d qdrant
# then set in .env:
QDRANT_URL=http://localhost:6333
./scripts/ingest.sh
```

Dashboard at http://localhost:6333/dashboard.

## Optional — PostgreSQL

```bash
docker compose -f docker/docker-compose.yml up -d postgres
./backend/.venv/bin/pip install asyncpg
# then set in .env:
DATABASE_URL=postgresql+asyncpg://gc:gc_local_dev_only@localhost:5432/gardencity
```

No model or query changes are needed — every column type is portable.

## Optional — Asterisk / a real phone number

See [docs/ASTERISK.md](docs/ASTERISK.md). Note that **a real phone number
cannot be free** — it requires a SIP or telephony provider.

---

## Troubleshooting

### Always check health first

```bash
curl -s http://127.0.0.1:8000/api/health | python3 -m json.tool
```

Every unhealthy service returns a `hint` with the command that fixes it.

### Common problems

| Symptom | Cause | Fix |
|---|---|---|
| `llm: unavailable` | Ollama not running | `brew services start ollama` |
| `Model 'qwen3:8b' is not installed` | Model not pulled | `ollama pull qwen3:8b` |
| `vector_store: 0 chunks` | Never ingested | `./scripts/crawl.sh && ./scripts/ingest.sh` |
| Ingest: "server holds the lock" | Embedded Qdrant is single-process | Stop the API, or use server mode |
| First reply takes ~10s | Cold model load | Normal once; `OLLAMA_KEEP_ALIVE=30m` keeps it warm |
| Every reply takes 10s+ | Model reloading or RAM pressure | Close other apps; try `llama3.2:3b` |
| Assistant escalates everything | Empty or stale index | Re-run ingestion |
| Assistant answers too confidently | Thresholds too low | Raise `CONFIDENCE_HIGH` in `.env` |
| Mic permission denied | Browser blocked it | Allow and reload; typing always works |
| `EADDRINUSE :5173` | Port taken | Change `server.port` in `frontend/vite.config.ts` |
| CORS errors | Frontend on an unexpected port | Add it to `CORS_ORIGINS` in `.env` |

### Reset everything

```bash
pkill -f 'uvicorn app.main:app'
rm -rf data/qdrant data/gardencity.db
./scripts/ingest.sh
```

This rebuilds the index and clears all conversations. Crawled pages are kept
(no need to re-fetch from the Village).

### Where things live

| What | Where |
|---|---|
| Conversations, escalations, review queue | `data/gardencity.db` |
| Vectors | `data/qdrant/` |
| Crawled Village pages | `knowledge/_crawled/` |
| Whisper + Piper models | `data/models/` |
| Settings | `.env` |
| Department rules | `config/departments.yaml` |
| Confidence weights | `config/confidence.yaml` |
