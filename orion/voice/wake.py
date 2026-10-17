"""Offline speech on Windows: wake word, instant commands, and a backup voice.

One persistent hidden process runs Windows' built-in recogniser and synthesiser (System.Speech, part of every
Windows install). It's started once, so nothing pays a start-up delay per command:

* **Wake word + instant command, in one breath.** It listens for your wake phrases ("Hey ORION", "Friday"…),
  either on their own, followed by any speech ("Hey ORION, what's the weather?"), or followed by one of the
  instant commands from commands.py ("Hey ORION, pause the music"). A recognised command runs locally without
  any transcription or Gemini request. Sentences that merely mention the name don't wake it.
* **REC** checks a recorded utterance for an instant command, and returns an offline dictation transcript as a
  quick preview and fallback.
* **SAY** speaks text with an installed Windows voice: the offline backup when the neural voice is unreachable.

Grammar choices were measured on real Windows (scripts/wake_lab.ps1): phrase + optional wildcard, with NO
dictation grammar competing on the wake listener (dictation steals the phrase: "Hey Orion" → "The Ryan").
Nothing leaves the PC; audio goes to Gemini only after a wake phrase is heard.
"""

from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import io
import itertools
import logging
import os
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Callable

log = logging.getLogger("orion.wake")

CS_SOURCE = r"""
using System; using System.Collections.Generic; using System.IO; using System.Linq; using System.Threading;
using System.Globalization; using System.Speech.Recognition; using System.Speech.Synthesis; using System.Speech.AudioFormat;
public static class OrionSpeech {
  static readonly object Out = new object();
  static void Say(string s) { lock (Out) { Console.Out.WriteLine(s); Console.Out.Flush(); } }
  static string F(double d) { return d.ToString("0.000", CultureInfo.InvariantCulture); }
  static string Clean(string s) { return (s ?? "").Replace("\r", " ").Replace("\n", " ").Replace("\t", " "); }
  static GrammarBuilder B(CultureInfo c) { var g = new GrammarBuilder(); g.Culture = c; return g; }
  static Grammar Named(GrammarBuilder gb, string name) { var g = new Grammar(gb); g.Name = name; return g; }
  static double MinConf(RecognizedPhrase r) {
    var words = r.Words.Where(w => w.Text != "..." && !string.IsNullOrEmpty(w.Text)).ToList();
    return words.Count == 0 ? r.Confidence : words.Min(w => (double)w.Confidence);  // the weakest word decides
  }
  static RecognizerInfo Pick(string culture) {
    var all = SpeechRecognitionEngine.InstalledRecognizers();
    if (all.Count == 0) return null;
    RecognizerInfo info = null;
    if (!string.IsNullOrEmpty(culture)) {
      info = all.FirstOrDefault(r => r.Culture.Name == culture)
          ?? all.FirstOrDefault(r => r.Culture.TwoLetterISOLanguageName == culture.Substring(0, 2));
    }
    return info ?? all.FirstOrDefault(r => r.Culture.Name == CultureInfo.CurrentUICulture.Name) ?? all[0];
  }
  static string Dictate(SpeechRecognitionEngine rec, string wav, out double conf) {
    var parts = new List<string>(); var confs = new List<double>(); var done = new ManualResetEvent(false);
    EventHandler<SpeechRecognizedEventArgs> ok = (s, e) => { parts.Add(e.Result.Text); confs.Add(e.Result.Confidence); };
    EventHandler<RecognizeCompletedEventArgs> fin = (s, e) => done.Set();
    rec.SpeechRecognized += ok; rec.RecognizeCompleted += fin;
    rec.SetInputToWaveFile(wav);
    rec.RecognizeAsync(RecognizeMode.Multiple);
    done.WaitOne(30000);
    rec.SpeechRecognized -= ok; rec.RecognizeCompleted -= fin;
    rec.SetInputToNull();
    conf = confs.Count == 0 ? 0 : confs.Average();
    return string.Join(" ", parts);
  }
  public static int Run(string culture, string specPath, string wav, bool mic) {
    var wake = new List<string>(); var cmds = new List<string>();
    foreach (var line in File.ReadAllLines(specPath, System.Text.Encoding.UTF8)) {
      if (line.StartsWith("W\t")) wake.Add(line.Substring(2)); else if (line.StartsWith("C\t")) cmds.Add(line.Substring(2));
    }
    var info = Pick(culture);
    if (info == null) { Say("ERR:NORECOGNIZER"); return 3; }
    var c = info.Culture;
    var engines = new List<SpeechRecognitionEngine>(); int pending = 0; var done = new ManualResetEvent(false);
    if ((mic || !string.IsNullOrEmpty(wav)) && wake.Count > 0) {
      // Two recognisers on the same audio. One hears the wake phrase + anything (proven reliable on its own); the
      // other hears the wake phrase + an instant command. In a single recogniser they compete, and a question like
      // "Hey Orion, what's the weather?" gets rejected by both.
      var lead = B(c); lead.Append(new Choices(wake.ToArray())); lead.AppendWildcard();
      var grammars = new List<Grammar> { Named(lead, "wake") };
      if (cmds.Count > 0) { var wc = B(c); wc.Append(new Choices(wake.ToArray())); wc.Append(new Choices(cmds.ToArray())); grammars.Add(Named(wc, "wakecmd")); }
      foreach (var g in grammars) {
        var tag = g.Name == "wake" ? "HIT:" : "CMD:";
        var listener = new SpeechRecognitionEngine(info);
        try { listener.UpdateRecognizerSetting("AdaptationOn", 0); } catch { }  // always-on mic: don't drift
        listener.LoadGrammar(g);
        listener.SpeechRecognized += (s, e) => Say(tag + F(MinConf(e.Result)) + ":" + Clean(e.Result.Text));
        if (tag == "HIT:") {
          listener.SpeechRecognitionRejected += (s, e) => {  // near-misses: shown in Settings for tuning, never a wake
            var a = e.Result == null ? null : e.Result.Alternates.FirstOrDefault(x => x.Grammar != null && x.Grammar.Name == "wake");
            if (a != null && a.Confidence >= 0.3) Say("NEAR:" + F(MinConf(a)) + ":" + Clean(a.Text));
          };
        }
        listener.RecognizeCompleted += (s, e) => { if (e.Error != null) Say("ERR:" + Clean(e.Error.Message)); if (Interlocked.Decrement(ref pending) == 0) done.Set(); };
        if (!string.IsNullOrEmpty(wav)) { listener.SetInputToWaveFile(wav); }
        else { try { listener.SetInputToDefaultAudioDevice(); } catch { Say("ERR:NOMIC"); return 4; } }
        engines.Add(listener); pending++;
      }
      foreach (var l in engines) l.RecognizeAsync(RecognizeMode.Multiple);
    }
    Say("READY:" + c.Name);
    if (!string.IsNullOrEmpty(wav)) { done.WaitOne(); Say("END"); return 0; }
    SpeechRecognitionEngine cmdRec = null, dictRec = null; SpeechSynthesizer synth = null;
    string cmdline;
    while ((cmdline = Console.In.ReadLine()) != null) {
      var p = cmdline.Split(' ');
      var id = p.Length > 1 ? p[1] : "0";
      try {
        if (p[0] == "REC" && p.Length >= 3) {  // instant-command check: small grammar, fast
          var path = Clean(System.Text.Encoding.UTF8.GetString(Convert.FromBase64String(p[2])));
          double cc = -1; string ct = "";
          if (cmds.Count > 0) {
            if (cmdRec == null) {
              cmdRec = new SpeechRecognitionEngine(info);
              var g = B(c); g.Append(new Choices(cmds.ToArray())); cmdRec.LoadGrammar(Named(g, "cmd"));
            }
            cmdRec.SetInputToWaveFile(path);
            var r = cmdRec.Recognize();
            cmdRec.SetInputToNull();
            if (r != null) { cc = MinConf(r); ct = r.Text; }
          }
          Say("REC " + id + " " + F(cc) + " " + Clean(ct));
        } else if (p[0] == "DIC" && p.Length >= 3) {  // offline dictation: quick preview + fallback transcript
          var path = Clean(System.Text.Encoding.UTF8.GetString(Convert.FromBase64String(p[2])));
          if (dictRec == null) { dictRec = new SpeechRecognitionEngine(info); var d = new DictationGrammar(); d.Name = "dict"; dictRec.LoadGrammar(d); }
          double dconf; var dt = Dictate(dictRec, path, out dconf);
          Say("DIC " + id + " " + F(dt.Length > 0 ? dconf : -1) + " " + Clean(dt));
        } else if (p[0] == "SAY" && p.Length >= 6) {
          if (synth == null) synth = new SpeechSynthesizer();
          var text = System.Text.Encoding.UTF8.GetString(Convert.FromBase64String(p[5]));
          var gender = p[3] == "female" ? VoiceGender.Female : VoiceGender.Male;
          try { synth.SelectVoiceByHints(gender, VoiceAge.Adult, 0, new CultureInfo(p[2])); } catch { }
          int rate; if (int.TryParse(p[4], out rate)) synth.Rate = Math.Max(-10, Math.Min(10, rate));
          var outPath = Path.Combine(Path.GetTempPath(), "orion-say-" + id + ".wav");
          synth.SetOutputToWaveFile(outPath, new SpeechAudioFormatInfo(24000, AudioBitsPerSample.Sixteen, AudioChannel.Mono));
          synth.Speak(text);
          synth.SetOutputToNull();
          Say("SAID " + id + " " + outPath);
        } else if (p[0] == "QUIT") { break; }
      } catch (Exception ex) { Say("FAIL " + id + " " + Clean(ex.Message)); }
    }
    return 0;
  }
}
"""

PS_SCRIPT = r"""
param([string]$Spec, [string]$Culture = '', [string]$Wav = '', [int]$Mic = 0, [string]$Dll = '')
$ErrorActionPreference = 'Stop'
function Say([string]$s) { [Console]::Out.WriteLine($s); [Console]::Out.Flush() }
try { Add-Type -AssemblyName System.Speech } catch { Say 'ERR:NOSPEECH'; exit 2 }
try {
  if ($Dll -and (Test-Path $Dll)) { Add-Type -Path $Dll }
  else {
    $src = [System.IO.File]::ReadAllText($Spec + '.cs')
    if ($Dll) { Add-Type -TypeDefinition $src -ReferencedAssemblies System.Speech, System.Core -OutputAssembly $Dll -OutputType Library; Add-Type -Path $Dll }
    else { Add-Type -TypeDefinition $src -ReferencedAssemblies System.Speech, System.Core }
  }
} catch { Say ('ERR:COMPILE ' + $_.Exception.Message); exit 5 }
exit [OrionSpeech]::Run($Culture, $Spec, $Wav, $Mic -eq 1)
"""

MERGE_WINDOW = 0.35  # seconds to wait for the other recogniser's verdict on the same utterance

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
    out: list[str] = []
    for p in phrases:
        # "ORION" would be read as an acronym (O-R-I-O-N); "Orion" is said as a word.
        words = [w.capitalize() if w.isupper() and len(w) > 1 else w for w in str(p).replace(",", " ").split()]
        p = " ".join(words)[:40]
        if len(p) >= 2 and p.lower() not in (x.lower() for x in out):
            out.append(p)
    return out[:8]


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
    for tag, kind in (("HIT:", "hit"), ("CMD:", "cmd"), ("NEAR:", "near")):
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


def parse_reply(line: str) -> tuple[str, str, float, str] | None:
    """'REC 7 0.912 pause the music' → ("REC", "7", 0.912, "pause the music"); same shape for DIC."""
    parts = line.rstrip("\r\n").split(" ", 3)
    if len(parts) < 3 or parts[0] not in ("REC", "DIC"):
        return None
    try:
        conf = float(parts[2])
    except ValueError:
        return None
    return parts[0], parts[1], conf, (parts[3] if len(parts) > 3 else "").strip()


def _paths() -> tuple[Path, Path]:
    """Script and compiled-DLL paths, versioned by the source so upgrades recompile once."""
    tag = hashlib.sha1((CS_SOURCE + PS_SCRIPT).encode()).hexdigest()[:10]
    base = Path(tempfile.gettempdir())
    ps = base / f"orion-speech-{tag}.ps1"
    if not ps.exists():
        ps.write_text(PS_SCRIPT, "utf-8")
    return ps, base / f"orion-speech-{tag}.dll"


def _write_spec(phrases: list[str], commands: list[str]) -> Path:
    fd, name = tempfile.mkstemp(prefix="orion-speech-", suffix=".spec")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join([f"W\t{p}" for p in phrases] + [f"C\t{c}" for c in commands]))
    Path(name + ".cs").write_text(CS_SOURCE, "utf-8")
    return Path(name)


def _command(spec: Path, culture: str = "", wav: str = "", mic: bool = False) -> list[str]:
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0",
                       "powershell.exe")
    ps, dll = _paths()
    cmd = [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(ps), "-Spec", str(spec),
           "-Mic", "1" if mic else "0", "-Dll", str(dll)]
    if culture:
        cmd += ["-Culture", culture]
    if wav:
        cmd += ["-Wav", wav]
    return cmd


def wav_pcm(path: str | Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as w:
        return w.readframes(w.getnframes()), w.getframerate()


def detect_in_wav(wav: str, phrases: list[str], culture: str = "", timeout: float = 120,
                  catalog: dict[str, str] | None = None) -> dict:
    """Run the wake listener over a WAV file: the same path as live listening, used by tests and the e2e."""
    from ..tools.platform import CREATE_NO_WINDOW
    from .commands import lookup

    clean = clean_phrases(phrases)
    spec = _write_spec(clean, list(catalog or {}))
    try:
        res = subprocess.run(_command(spec, culture, wav), capture_output=True, timeout=timeout,
                             creationflags=CREATE_NO_WINDOW)
    finally:
        for f in (spec, Path(str(spec) + ".cs")):
            f.unlink(missing_ok=True)
    out: dict = {"ready": None, "hits": [], "commands": [], "near": [], "error": None}
    for raw in (res.stdout or b"").decode("utf-8", "replace").splitlines():
        p = parse_line(raw)
        if not p:
            continue
        if p[0] == "ready":
            out["ready"] = p[2]
        elif p[0] in ("hit", "near"):
            out["hits" if p[0] == "hit" else "near"].append({"phrase": match_phrase(p[2], clean), "confidence": p[1]})
        elif p[0] == "cmd" and catalog:
            out["commands"].append({"phrase": match_phrase(p[2], clean), "confidence": p[1],
                                    "command": lookup(catalog, p[2], clean)})
        elif p[0] == "err":
            out["error"] = ERRORS.get(p[2], p[2])
    if res.returncode not in (0, None) and not out["error"]:
        out["error"] = (res.stderr or b"").decode("utf-8", "replace").strip()[:300] or f"exit {res.returncode}"
    return out


class SpeechService:
    """The persistent Windows speech process: wake listener (optional), REC and SAY requests."""

    def __init__(self, phrases: list[str], catalog: dict[str, str], culture: str = "", listen: bool = True,
                 sensitivity: float = 0.5,
                 on_hit: Callable[[str, float, str | None], None] = lambda *a: None,
                 on_heard: Callable[[str, float, bool], None] = lambda *a: None,
                 on_error: Callable[[str], None] = lambda *a: None) -> None:
        self.phrases = clean_phrases(phrases)
        if listen and not self.phrases:
            raise ValueError("Add at least one wake phrase.")
        self.catalog = catalog
        self.culture = culture
        self.listen = listen
        self.threshold = threshold_for(sensitivity)
        self.on_hit, self.on_heard, self.on_error = on_hit, on_heard, on_error
        self.proc: subprocess.Popen | None = None
        self.language = ""
        self.fatal: str | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._ids = itertools.count(1)
        self._pending: dict[str, concurrent.futures.Future] = {}
        self._write_lock = threading.Lock()
        self._spec: Path | None = None
        self._held: list | None = None
        self._merge_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="speech-service", daemon=True)

    def start(self, wait: float = 45) -> bool:
        self._thread.start()
        return self._ready.wait(wait)

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None and bool(self.language)

    def _handle(self, raw: str) -> None:
        from .commands import lookup

        if raw.startswith(("REC ", "DIC ")):
            parsed = parse_reply(raw)
            if parsed:
                fut = self._pending.pop(parsed[1], None)
                if fut and not fut.done():
                    fut.set_result((parsed[2], parsed[3]))
            return
        if raw.startswith(("SAID ", "FAIL ")):
            kind, rid, rest = (raw.rstrip("\r\n").split(" ", 2) + [""])[:3]
            fut = self._pending.pop(rid, None)
            if fut and not fut.done():
                if kind == "SAID":
                    fut.set_result(rest)
                else:
                    fut.set_exception(RuntimeError(rest))
            return
        p = parse_line(raw)
        if not p:
            return
        kind, conf, text = p
        if kind == "ready":
            self.language = text
            self._ready.set()
        elif kind == "err":
            msg = ERRORS.get(text, text)
            if text in ("NOSPEECH", "NORECOGNIZER") or text.startswith("COMPILE"):
                self.fatal = msg
            self.on_error(msg)
            self._ready.set()
        elif kind == "near":
            self.on_heard(match_phrase(text, self.phrases), conf, False)
        elif kind in ("hit", "cmd"):
            phrase = match_phrase(text, self.phrases)
            command = lookup(self.catalog, text, self.phrases) if kind == "cmd" else None
            if kind == "hit":
                self.on_heard(phrase, conf, conf >= self.threshold)
            if conf < self.threshold:
                return
            self._merge(phrase, conf, command)

    def _merge(self, phrase: str, conf: float, command: str | None) -> None:
        """The wake and command recognisers each report the same utterance: send one hit, with the command if any."""
        with self._merge_lock:
            if self._held is None:
                self._held = [phrase, conf, command]
                t = threading.Timer(MERGE_WINDOW, self._release)
                t.daemon = True
                t.start()
            elif command and not self._held[2]:
                self._held = [phrase, conf, command]

    def _release(self) -> None:
        with self._merge_lock:
            held, self._held = self._held, None
        if held:
            self.on_hit(*held)

    def _run(self) -> None:
        from ..tools.platform import CREATE_NO_WINDOW

        self._spec = _write_spec(self.phrases if self.listen else [], list(self.catalog))
        backoff = 2.0
        try:
            while not self._stop.is_set():
                try:
                    self.proc = subprocess.Popen(_command(self._spec, self.culture, mic=self.listen),
                                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                 stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
                except OSError as exc:
                    self.fatal = f"Couldn't start Windows speech recognition: {exc}"
                    self.on_error(self.fatal)
                    self._ready.set()
                    return
                for raw in iter(self.proc.stdout.readline, b""):  # type: ignore[union-attr]
                    self._handle(raw.decode("utf-8", "replace"))
                self.language = ""
                for fut in list(self._pending.values()):
                    if not fut.done():
                        fut.set_exception(RuntimeError("speech service restarted"))
                self._pending.clear()
                if self._stop.is_set() or self.fatal:
                    break
                time.sleep(backoff)  # stopped (device change, sleep/resume…): restart it
                backoff = min(backoff * 2, 60)
        finally:
            self._ready.set()
            if self._spec:
                for f in (self._spec, Path(str(self._spec) + ".cs")):
                    f.unlink(missing_ok=True)

    def _request(self, verb: str, args: str, timeout: float) -> object:
        if not self.running:
            raise RuntimeError("speech service isn't running")
        rid = str(next(self._ids))
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._pending[rid] = fut
        with self._write_lock:
            self.proc.stdin.write(f"{verb} {rid} {args}\n".encode())  # type: ignore[union-attr]
            self.proc.stdin.flush()  # type: ignore[union-attr]
        try:
            return fut.result(timeout)
        finally:
            self._pending.pop(rid, None)

    def match_command(self, wav_path: str | Path, timeout: float = 8) -> tuple[str | None, float, str]:
        """Is this utterance one of the instant commands? → (canonical or None, confidence, recognised text)."""
        from .commands import lookup

        if not self.catalog:
            return None, 0.0, ""
        b64 = base64.b64encode(str(wav_path).encode()).decode()
        conf, text = self._request("REC", b64, timeout)  # type: ignore[misc]
        return (lookup(self.catalog, text) if conf >= 0 else None), max(conf, 0.0), text

    def dictate(self, wav_path: str | Path, timeout: float = 20) -> tuple[str, float]:
        """Offline transcript of an utterance (rougher than Gemini's): the quick preview and the no-internet fallback."""
        b64 = base64.b64encode(str(wav_path).encode()).decode()
        conf, text = self._request("DIC", b64, timeout)  # type: ignore[misc]
        return text, max(conf, 0.0)

    def synthesize(self, text: str, culture: str = "en-GB", gender: str = "male", rate: int = 1,
                   timeout: float = 30) -> bytes:
        """Speak with an installed Windows voice; returns 24 kHz mono 16-bit PCM."""
        b64 = base64.b64encode(text.encode()).decode()
        path = str(self._request("SAY", f"{culture} {gender} {rate} {b64}", timeout))
        try:
            pcm, _ = wav_pcm(path)
            return pcm
        finally:
            Path(path).unlink(missing_ok=True)

    def stop(self) -> None:
        self._stop.set()
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write(b"QUIT\n")  # type: ignore[union-attr]
                self.proc.stdin.flush()  # type: ignore[union-attr]
                self.proc.wait(2)
            except Exception:
                self.proc.kill()


def pcm_from_wav_bytes(data: bytes) -> bytes:
    with wave.open(io.BytesIO(data), "rb") as w:
        return w.readframes(w.getnframes())
