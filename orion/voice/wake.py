"""Wake word: say any phrase you choose ("Hey ORION", "Jarvis", "Friday"…) to start talking.

Default engine: Windows' built-in offline speech recogniser (System.Speech, part of every Windows install).
It listens only for the chosen phrases at the start of an utterance, optionally followed by a command ("Hey
ORION, what's the weather?"). Sentences that merely mention the name don't wake it. Unusual names work too: Windows works out how they're pronounced. Nothing
leaves the PC; audio goes to Gemini only after a wake phrase is heard.

Optional engine: openWakeWord's pre-trained models (hey_jarvis, alexa…) when that package is installed.
"""

from __future__ import annotations

import base64
import logging
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger("orion.wake")

PS_SCRIPT = r"""
param([string]$Phrases, [string]$Culture = '', [string]$Wav = '')
$ErrorActionPreference = 'Stop'
function Say([string]$s) { [Console]::Out.WriteLine($s); [Console]::Out.Flush() }
try { Add-Type -AssemblyName System.Speech } catch { Say 'ERR:NOSPEECH'; exit 2 }
# Grammar choice measured on real Windows (scripts/wake_lab.ps1): the phrase followed by an optional wildcard and NO
# dictation grammar. Dictation competes with the wake phrase and steals it ("Hey Orion" -> "The Ryan"); a phrase-only
# grammar false-wakes on sentences that merely contain the name. Phrase + wildcard caught every phrase at ~0.95 and
# rejected both kinds of trap at 0.00.
$src = @'
using System; using System.Linq; using System.Speech.Recognition; using System.Threading; using System.Globalization;
public static class OrionWake {
  static void Say(string s) { Console.Out.WriteLine(s); Console.Out.Flush(); }
  static string F(double d) { return d.ToString("0.000", CultureInfo.InvariantCulture); }
  static GrammarBuilder B(CultureInfo c) { var g = new GrammarBuilder(); g.Culture = c; return g; }
  static double PhraseConfidence(RecognizedPhrase r) {
    var words = r.Words.Where(w => w.Text != "..." && !string.IsNullOrEmpty(w.Text)).ToList();
    return words.Count == 0 ? r.Confidence : words.Min(w => (double)w.Confidence);  // the weakest wake word decides
  }
  public static int Run(string culture, string[] phrases, string wav) {
    var all = SpeechRecognitionEngine.InstalledRecognizers();
    if (all.Count == 0) { Say("ERR:NORECOGNIZER"); return 3; }
    RecognizerInfo info = null;
    if (!string.IsNullOrEmpty(culture)) {
      info = all.FirstOrDefault(r => r.Culture.Name == culture)
          ?? all.FirstOrDefault(r => r.Culture.TwoLetterISOLanguageName == culture.Substring(0, 2));
    }
    info = info ?? all.FirstOrDefault(r => r.Culture.Name == CultureInfo.CurrentUICulture.Name) ?? all[0];
    using (var rec = new SpeechRecognitionEngine(info)) {
      var c = info.Culture;
      var alone = B(c); alone.Append(new Choices(phrases));
      var lead = B(c); lead.Append(new Choices(phrases)); lead.AppendWildcard();
      var gb = B(c); gb.Append(new Choices(alone, lead));
      var g = new Grammar(gb); g.Name = "wake";
      rec.LoadGrammar(g);
      var done = new ManualResetEvent(false);
      rec.SpeechRecognized += (s, e) => Say("HIT:" + F(PhraseConfidence(e.Result)) + ":" + e.Result.Text);
      rec.SpeechRecognitionRejected += (s, e) => {
        if (e.Result != null && e.Result.Alternates.Count > 0 && e.Result.Alternates[0].Confidence >= 0.3)
          Say("NEAR:" + F(PhraseConfidence(e.Result.Alternates[0])) + ":" + e.Result.Alternates[0].Text);
      };
      rec.RecognizeCompleted += (s, e) => {
        if (e.Error != null) Say("ERR:" + e.Error.Message.Replace("\r", " ").Replace("\n", " "));
        done.Set();
      };
      if (string.IsNullOrEmpty(wav)) {
        try { rec.SetInputToDefaultAudioDevice(); } catch { Say("ERR:NOMIC"); return 4; }
      } else { rec.SetInputToWaveFile(wav); }
      Say("READY:" + c.Name);
      rec.RecognizeAsync(RecognizeMode.Multiple);
      done.WaitOne();  // a WAV ends by itself; live listening runs until the process is stopped
      Say("END");
      return 0;
    }
  }
}
'@
try { Add-Type -TypeDefinition $src -ReferencedAssemblies System.Speech, System.Core } catch { Say ('ERR:COMPILE ' + $_.Exception.Message); exit 5 }
# One phrase per line (ConvertFrom-Json in PowerShell 5.1 would hand back the whole array as a single item).
$list = [string[]]@(([System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Phrases)) -split "`n") | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() })
exit [OrionWake]::Run($Culture, $list, $Wav)
"""

ERRORS = {
    "NOSPEECH": "Windows speech components aren't available on this PC.",
    "NORECOGNIZER": "No Windows speech recogniser is installed. Add one in Windows Settings → Time & language → "
                    "Speech (a language with speech recognition, e.g. English or Spanish).",
    "NOMIC": "Windows couldn't open the default microphone for the wake word.",
}


def threshold_for(sensitivity: float) -> float:
    """0 = strict (few false alarms) … 1 = relaxed (catches quiet/accented speech)."""
    return round(0.9 - 0.45 * max(0.0, min(1.0, sensitivity)), 3)


def clean_phrases(phrases: list[str]) -> list[str]:
    out = []
    for p in phrases:
        # "ORION" would be read as an acronym (O-R-I-O-N); "Orion" is said as a word.
        words = [w.capitalize() if w.isupper() and len(w) > 1 else w for w in str(p).replace(",", " ").split()]
        p = " ".join(words)[:40]
        if len(p) >= 2 and p.lower() not in (x.lower() for x in out):
            out.append(p)
    return out[:8]


def _script_path() -> Path:
    path = Path(tempfile.gettempdir()) / "orion-wake.ps1"
    if not path.exists() or path.read_text("utf-8", errors="ignore") != PS_SCRIPT:
        path.write_text(PS_SCRIPT, "utf-8")
    return path


def _command(phrases: list[str], culture: str = "", wav: str = "") -> list[str]:
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0",
                       "powershell.exe")
    b64 = base64.b64encode("\n".join(phrases).encode()).decode()
    cmd = [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(_script_path()),
           "-Phrases", b64]
    if culture:
        cmd += ["-Culture", culture]
    if wav:
        cmd += ["-Wav", wav]
    return cmd


def match_phrase(text: str, phrases: list[str]) -> str:
    """Map recogniser text ("Hey Orion what's the weather") back to the configured phrase."""
    low = " ".join(text.lower().replace("...", " ").split())
    for p in sorted(phrases, key=len, reverse=True):
        if low.startswith(p.lower()):
            return p
    return text.replace("...", "").strip()


def parse_line(line: str) -> tuple[str, float, str] | None:
    """'HIT:0.912:Hey ORION' → ("hit", 0.912, "Hey ORION"); READY/ERR/END → (kind, 0, detail)."""
    line = line.strip()
    for tag, kind in (("HIT:", "hit"), ("NEAR:", "near")):
        if line.startswith(tag):
            _, conf, text = line.split(":", 2)
            try:
                return kind, float(conf), text
            except ValueError:
                return None
    for kind in ("READY", "ERR"):
        if line.startswith(kind + ":"):
            return kind.lower(), 0.0, line.split(":", 1)[1]
    if line == "END":
        return "end", 0.0, ""
    return None


def detect_in_wav(wav: str, phrases: list[str], culture: str = "", timeout: float = 90) -> dict:
    """Run the Windows recogniser over a WAV file: the same path as live listening, used by tests and the e2e."""
    from ..tools.platform import CREATE_NO_WINDOW

    res = subprocess.run(_command(clean_phrases(phrases), culture, wav), capture_output=True, timeout=timeout,
                         creationflags=CREATE_NO_WINDOW)
    out = {"ready": None, "hits": [], "near": [], "error": None}
    for raw in (res.stdout or b"").decode("utf-8", "replace").splitlines():
        p = parse_line(raw)
        if not p:
            continue
        if p[0] == "ready":
            out["ready"] = p[2]
        elif p[0] in ("hit", "near"):
            out["hits" if p[0] == "hit" else "near"].append(
                {"phrase": match_phrase(p[2], clean_phrases(phrases)), "confidence": p[1]})
        elif p[0] == "err":
            out["error"] = ERRORS.get(p[2], p[2])
    if res.returncode not in (0, None) and not out["error"]:
        out["error"] = (res.stderr or b"").decode("utf-8", "replace").strip()[:300] or f"exit {res.returncode}"
    return out


class WindowsWake:
    """Keeps the recogniser running in a hidden PowerShell process and reports what it hears."""

    def __init__(self, phrases: list[str], sensitivity: float, culture: str,
                 on_hit: Callable[[str, float], None], on_heard: Callable[[str, float, bool], None],
                 on_error: Callable[[str], None]) -> None:
        self.phrases = clean_phrases(phrases)
        if not self.phrases:
            raise ValueError("Add at least one wake phrase.")
        self.threshold = threshold_for(sensitivity)
        self.culture = culture
        self.on_hit, self.on_heard, self.on_error = on_hit, on_heard, on_error
        self.proc: subprocess.Popen | None = None
        self.language = ""
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="wake-word", daemon=True)

    def start(self, wait: float = 20) -> None:
        self._thread.start()
        self._ready.wait(wait)

    def _run(self) -> None:
        from ..tools.platform import CREATE_NO_WINDOW

        backoff = 2.0
        while not self._stop.is_set():
            try:
                self.proc = subprocess.Popen(_command(self.phrases, self.culture), stdout=subprocess.PIPE,
                                             stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
            except OSError as exc:
                self.on_error(f"Couldn't start Windows speech recognition: {exc}")
                self._ready.set()
                return
            fatal = False
            for raw in iter(self.proc.stdout.readline, b""):  # type: ignore[union-attr]
                p = parse_line(raw.decode("utf-8", "replace"))
                if not p:
                    continue
                kind, conf, text = p
                if kind == "ready":
                    self.language = text
                    backoff = 2.0
                    self._ready.set()
                elif kind == "err":
                    self.on_error(ERRORS.get(text, text))
                    fatal = text in ("NOSPEECH", "NORECOGNIZER")
                    self._ready.set()
                elif kind == "near":  # close but rejected by Windows: shown in Settings to help tuning
                    self.on_heard(match_phrase(text, self.phrases), conf, False)
                elif kind == "hit":
                    text = match_phrase(text, self.phrases)
                    accepted = conf >= self.threshold
                    self.on_heard(text, conf, accepted)
                    if accepted:
                        self.on_hit(text, conf)
            if self._stop.is_set() or fatal:
                break
            time.sleep(backoff)  # recogniser stopped (device change, sleep/resume…): restart it
            backoff = min(backoff * 2, 60)
        self._ready.set()

    def stop(self) -> None:
        self._stop.set()
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
