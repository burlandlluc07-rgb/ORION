# Wake-word lab: how does Windows' offline recogniser (System.Speech) respond to different grammar shapes?
# Synthesises test sentences with Windows' own voice and prints exactly what was recognised or rejected.
# Run on Windows PowerShell 5.1:  powershell -ExecutionPolicy Bypass -File scripts\wake_lab.ps1
param([string]$Culture = 'en-US')
Add-Type -AssemblyName System.Speech
$src = @"
using System; using System.Collections.Generic; using System.Linq; using System.Speech.Recognition; using System.Threading;
public static class WakeLab {
  static GrammarBuilder B(System.Globalization.CultureInfo c) { var g = new GrammarBuilder(); g.Culture = c; return g; }
  public static string Run(string culture, string[] phrases, string mode, bool dict, string wav) {
    var info = SpeechRecognitionEngine.InstalledRecognizers().FirstOrDefault(r => r.Culture.Name == culture)
               ?? SpeechRecognitionEngine.InstalledRecognizers().First();
    using (var rec = new SpeechRecognitionEngine(info)) {
      var c = info.Culture;
      var gb = B(c);
      if (mode == "plain") { gb.Append(new Choices(phrases)); }
      else if (mode == "wild") { gb.Append(new Choices(phrases)); gb.AppendWildcard(); }
      else if (mode == "either") { var a = B(c); a.Append(new Choices(phrases)); var b = B(c); b.Append(new Choices(phrases)); b.AppendWildcard(); gb.Append(new Choices(a, b)); }
      else if (mode == "dictsuffix") { gb.Append(new Choices(phrases)); gb.AppendDictation(); }
      else if (mode == "twogrammars") { gb.Append(new Choices(phrases)); var w = B(c); w.Append(new Choices(phrases)); w.AppendWildcard(); var gw = new Grammar(w); gw.Name = "wake"; rec.LoadGrammar(gw); }
      var g = new Grammar(gb); g.Name = "wake"; rec.LoadGrammar(g);
      if (dict) { var d = new DictationGrammar(); d.Name = "other"; rec.LoadGrammar(d); }
      rec.SetInputToWaveFile(wav);
      var outl = new List<string>(); var done = new ManualResetEvent(false);
      rec.SpeechRecognized += (s, e) => outl.Add("OK[" + e.Result.Grammar.Name + " " + e.Result.Confidence.ToString("0.00") + "] '" + e.Result.Text + "' words=" + string.Join(",", e.Result.Words.Select(x => x.Text + "@" + x.Confidence.ToString("0.00"))));
      rec.SpeechRecognitionRejected += (s, e) => outl.Add("REJ" + (e.Result != null && e.Result.Alternates.Count > 0 ? " alt='" + e.Result.Alternates[0].Text + "'@" + e.Result.Alternates[0].Confidence.ToString("0.00") + " g=" + (e.Result.Alternates[0].Grammar == null ? "?" : e.Result.Alternates[0].Grammar.Name) : ""));
      rec.RecognizeCompleted += (s, e) => { if (e.InputStreamEnded || e.Error != null || e.Cancelled) { if (e.Error != null) outl.Add("ERROR " + e.Error.Message); done.Set(); } };
      rec.RecognizeAsync(RecognizeMode.Multiple);
      if (!done.WaitOne(60000)) outl.Add("TIMEOUT");
      return outl.Count == 0 ? "(nothing)" : string.Join(" | ", outl);
    }
  }
}
"@
Add-Type -TypeDefinition $src -ReferencedAssemblies System.Speech, System.Core
$dir = Join-Path $env:TEMP 'wakelab'; New-Item -ItemType Directory -Force $dir | Out-Null
$texts = [ordered]@{
  alone = 'Hey Orion.'; command = 'Hey Orion, what is the weather like today?'; friday = 'Friday, open my email please.'
  jarvis = 'Hey Jarvis, set a timer for five minutes.'; pizza = 'Let us order a pizza and watch a movie tonight.'
  mention = 'I watched a documentary about the Orion constellation last night.'
}
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
foreach ($k in $texts.Keys) { $synth.SetOutputToWaveFile((Join-Path $dir "$k.wav")); $synth.Speak($texts[$k]) }
$synth.SetOutputToNull(); $synth.Dispose()
"Recognisers: " + (([System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() | ForEach-Object { $_.Culture.Name }) -join ', ')
$phrases = [string[]]@('Hey Orion', 'Friday', 'Hey Jarvis')
foreach ($mode in 'plain', 'wild', 'either', 'dictsuffix', 'twogrammars') {
  foreach ($dict in $true, $false) {
    "=== mode=$mode dictation=$dict"
    foreach ($k in $texts.Keys) {
      try { $r = [WakeLab]::Run($Culture, $phrases, $mode, $dict, (Join-Path $dir "$k.wav")) } catch { $r = "EXCEPTION " + $_.Exception.InnerException.Message + $_.Exception.Message }
      "  {0,-8} {1}" -f $k, $r
    }
  }
}
