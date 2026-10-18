# Backup brain

Gemini's free quota is shared by every key in a Google project, so once the day's requests are used up, every
Gemini model stops answering until midnight Pacific time. The backup brain keeps ORION working through that:
when Gemini can't answer (quota used up, every model rate-limited or overloaded), chat, tools, missions and
routines carry on through a second, OpenAI-compatible provider. ORION goes back to Gemini as soon as Gemini
answers again. A toast tells you when it switches each way, and the chat shows `backup · <model>` as the model.

Set it up in **Settings → Brain → Backup brain**: pick a provider, paste a key if it needs one, and press
**Test & save**. ORION sends one tiny test request and only saves the setting if it gets an answer.

| Choice | What it is | Key |
| --- | --- | --- |
| **OmniRoute on this PC** | A free, open-source gateway you run yourself. It holds keys for many free providers and moves on to the next one when one runs out. Address `http://localhost:20128/v1`, model `auto`. | Only if you turned on its key check |
| **OpenRouter (free models)** | Hosted router with free models (names end in `:free`). ORION picks one when you test. | Free account key from openrouter.ai/keys |
| **Groq** | Fast open models with a free tier. | Free account key from console.groq.com/keys |
| **Custom** | Anything that speaks the OpenAI Chat Completions API, e.g. LM Studio or Ollama on this PC. | If the service needs one |

## What still needs Gemini

- **Voice.** Live voice uses Gemini's Live API, and Classic voice transcribes speech with Gemini. Neither moves
  to the backup.
- **Audio, video and documents sent to the model.** The backup takes text and images only. A request with
  audio or a PDF attached reports Gemini's own quota message instead.
- **The background agent** (Tasks → background) still uses its own Gemini or Claude setting.

## OmniRoute setup (Windows)

1. Install Node.js 24 LTS from nodejs.org (OmniRoute 3.8 needs Node 22.22.2+ or 24+).
2. In a terminal: `npm install -g omniroute`, then `omniroute`. It prints the dashboard address.
3. Keep it on this PC only: set `OMNIROUTE_SERVER_HOST=127.0.0.1` (in `%USERPROFILE%\.omniroute\.env`). By
   default it listens on every network address with no key, so anyone on your Wi-Fi could use your providers.
   Turning on `REQUIRE_API_KEY=true` and pasting an OmniRoute key into ORION is safer still.
4. In the dashboard (`http://localhost:20128`), connect a few free providers. The keyless default is not
   enough on its own: in our test, `auto` with no providers connected picked a model that asked for a key.
5. In ORION: Settings → Brain → Backup brain → **OmniRoute on this PC** → **Test & save**.

Notes:

- Don't connect several Google accounts or projects to stretch Gemini's free quota. Google counts quota per
  project and treats running several projects as one as quota circumvention; projects have been restricted
  for it. OmniRoute's own catalogue flags Gemini and a dozen other providers as "caution" or "avoid" on terms;
  check `/dashboard/free-tiers` before connecting one.
- OmniRoute stores your provider keys (encrypted) and sees every prompt it forwards. Leave its traffic
  interception, "stealth" and cloud-sync features off.

## Privacy and keys

The backup key is saved in Windows Credential Manager (entry `ORION` / `backup_api_key`), or in a private file
if the vault isn't available, and never in `settings.json`, logs or memory. Setting the
`ORION_BACKUP_API_KEY` environment variable overrides it. **Turn off and forget the saved key** removes it
from the vault. Requests to the backup carry the same conversation and tool list ORION would
have sent to Gemini.
