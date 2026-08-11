# Telephony: Asterisk, SIP, and a Real Phone Number

**Optional. Not required for the prototype, and deliberately not part of first
run** — it adds meaningful setup complexity for no demo benefit.

---

## Be clear about cost

**A real phone number cannot be free.** Software is free; carrier connectivity
is not. Anyone claiming otherwise is describing a trial credit.

| Option | Software cost | Carrier cost | Setup |
|---|---|---|---|
| **A. Browser demo** | $0 | $0 | ✅ already built |
| **B. Local Asterisk** | $0 | $0 (SIP softphone only, no PSTN) | ~2 hours |
| **C. Real phone number** | $0 | **~$1–2/month + ~$0.013/min** | ~1 day |

Option B gives you a real PBX and real SIP calls between softphones on your
network — genuinely useful for testing telephony behavior — but it **cannot
reach an actual phone number** without a trunk from a provider.

---

## Why the AI layer needs no changes

`backend/app/api/voice.py` accepts audio bytes and returns audio bytes. It does
not know or care about the source:

```
Browser  ─┐
Phone    ─┼─→  AudioIngress  →  Whisper → Router → RAG → LLM → Piper
SIP      ─┤                                    (identical for all)
WebRTC   ─┘
```

`channel` is recorded for analytics only. Adding telephony means writing a
bridge that posts audio to `/api/voice/turn` and plays back the response — the
reasoning pipeline is untouched.

---

## Option B — Local Asterisk

### Install

```bash
brew install asterisk          # macOS
sudo apt install asterisk      # Debian/Ubuntu
```

### Minimal configuration

`pjsip.conf` — one softphone extension:

```ini
[transport-udp]
type = transport
protocol = udp
bind = 0.0.0.0:5060

[6001]
type = endpoint
context = garden-city
disallow = all
; ulaw is 8kHz mono — matches what Whisper expects and avoids transcoding
allow = ulaw
auth = 6001-auth
aors = 6001

[6001-auth]
type = auth
auth_type = userpass
username = 6001
; Local testing only. Never reuse this anywhere real.
password = change_this_local_only

[6001]
type = aor
max_contacts = 1
```

`extensions.conf` — route calls to the AI bridge:

```ini
[garden-city]
; Dial 1000 to reach the assistant
exten => 1000,1,Answer()
 same  =>      n,Wait(1)
 same  =>      n,Playback(hello-world)
 same  =>      n,AGI(garden_city_ai.agi)
 same  =>      n,Hangup()

; Simulated department transfers become real ones here
exten => 2001,1,Dial(PJSIP/public_works,30)
exten => 2002,1,Dial(PJSIP/recreation,30)
exten => 2003,1,Dial(PJSIP/building,30)
exten => 2004,1,Dial(PJSIP/village_clerk,30)
```

### Connect a softphone

Install [Zoiper](https://www.zoiper.com/) or [Linphone](https://www.linphone.org/),
register as extension `6001` against your machine's IP, and dial `1000`.

### The bridge

You need an AGI or ARI script that:

1. Records caller audio until silence (Asterisk's `Record()` with a silence timeout, or ARI snoop for streaming)
2. `POST`s the audio to `http://127.0.0.1:8000/api/voice/turn` with `channel=phone`
3. Fetches speech from `/api/voice/synthesize`
4. Plays it back
5. On an `escalate` action, dials the department extension instead

**This bridge is not included.** It is a genuine piece of work — a few hundred
lines with real edge cases around barge-in, DTMF, and call teardown — and it
would sit unused in a browser-only demo.

Recommended approach: **ARI (Asterisk REST Interface) over AGI.** ARI supports
streaming media and external media channels, which makes interruption handling
far cleaner than AGI's turn-based model.

---

## Option C — A real phone number

### Path 1: Twilio (simplest)

1. Buy a number (~$1.15/month)
2. Point its Voice webhook at your backend
3. Use Twilio Media Streams to send audio over a WebSocket
4. Bridge that WebSocket to `/api/voice/turn`

**Cost:** number + ~$0.0085/min inbound. A pilot at 100 calls/day averaging
3 minutes is roughly **$80/month**.

Your backend needs a public HTTPS URL — `cloudflared tunnel` works for testing;
production needs real hosting (see [PRODUCTION_ROADMAP.md](../PRODUCTION_ROADMAP.md)).

### Path 2: SIP trunk into Asterisk

Providers: Flowroute, Telnyx, VoIP.ms, Bandwidth.

Usually cheaper per minute and keeps media on Village infrastructure — which
often matters more than cost for a municipality, since resident audio never
transits a third-party API.

```ini
[trunk-provider]
type = registration
transport = transport-udp
outbound_auth = trunk-auth
server_uri = sip:sip.provider.example
client_uri = sip:VILLAGE_DID@sip.provider.example
```

Credentials belong in a secret store, not in a committed config file.

---

## Telephony-specific requirements

Things that don't matter in a browser and matter a great deal on a phone:

| Concern | Why it changes |
|---|---|
| **Audio format** | Phone audio is 8 kHz μ-law. Whisper wants 16 kHz. Resample, don't just reinterpret. |
| **Barge-in** | Callers interrupt constantly. Needs echo cancellation so the assistant doesn't hear itself. |
| **Latency budget** | Silence over 2s reads as a dropped call. Current warm turn is ~3s — acceptable, but the greeting should start immediately. |
| **DTMF fallback** | Offer "press 1 for…" for callers who prefer keypads or have speech differences. |
| **Call teardown** | Hangups mid-turn must clean up sessions and not orphan work. |
| **Recording disclosure** | Legally required in most jurisdictions. Must be spoken at call start. |
| **911** | The assistant must **never** attempt to handle an emergency. The policy override exists; on a phone line, confirm the carrier path to emergency services is unaffected. |
| **Accessibility** | Callers using relay services, TTY, or with atypical speech must reach a human easily. |

---

## Recommended sequence

1. **Browser demo** (done) — proves the AI works
2. **Local Asterisk + softphone** — proves the telephony bridge works, $0
3. **SIP trunk or Twilio number, staff-only** — real phone, real handsets, no residents
4. **Pilot on one department** — publish the number narrowly, keep human fallback
5. **Expand** only after measured accuracy holds

Do not skip step 2. Debugging an audio bridge and a carrier connection at the
same time is much harder than debugging them separately.
