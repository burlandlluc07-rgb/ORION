// Push-to-talk recorder: captures the mic with Web Audio and encodes 16 kHz mono WAV
// (works on Android Chrome and iOS Safari alike; MediaRecorder formats differ per browser).

export class WavRecorder {
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: ScriptProcessorNode | null = null;
  private chunks: Float32Array[] = [];
  private rate = 48000;
  level = 0;

  static supported(): boolean {
    return !!(navigator.mediaDevices?.getUserMedia) && window.isSecureContext;
  }

  async start(): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    const Ctx = window.AudioContext || (window as any).webkitAudioContext;
    this.ctx = new Ctx();
    this.rate = this.ctx.sampleRate;
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.node = this.ctx.createScriptProcessor(4096, 1, 1);
    this.chunks = [];
    this.node.onaudioprocess = (e) => {
      const data = new Float32Array(e.inputBuffer.getChannelData(0));
      this.chunks.push(data);
      let sum = 0;
      for (let i = 0; i < data.length; i += 16) sum += data[i] * data[i];
      this.level = Math.min(1, Math.sqrt(sum / (data.length / 16)) * 6);
    };
    src.connect(this.node);
    this.node.connect(this.ctx.destination);
  }

  async stop(): Promise<Blob> {
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    await this.ctx?.close();
    this.level = 0;
    return encodeWav(downsample(concat(this.chunks), this.rate, 16000), 16000);
  }
}

function concat(chunks: Float32Array[]): Float32Array {
  const out = new Float32Array(chunks.reduce((n, c) => n + c.length, 0));
  let o = 0;
  for (const c of chunks) { out.set(c, o); o += c.length; }
  return out;
}

function downsample(buf: Float32Array, from: number, to: number): Float32Array {
  if (from === to) return buf;
  const ratio = from / to;
  const out = new Float32Array(Math.floor(buf.length / ratio));
  for (let i = 0; i < out.length; i++) {
    const start = Math.floor(i * ratio), end = Math.min(buf.length, Math.floor((i + 1) * ratio));
    let s = 0;
    for (let j = start; j < end; j++) s += buf[j];
    out[i] = s / Math.max(1, end - start);
  }
  return out;
}

function encodeWav(samples: Float32Array, rate: number): Blob {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const str = (o: number, s: string) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  str(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); str(8, "WAVE"); str(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, "data");
  v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buf], { type: "audio/wav" });
}

export function blobToB64(b: Blob): Promise<string> {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(String(r.result).split(",", 2)[1] ?? "");
    r.onerror = () => rej(r.error);
    r.readAsDataURL(b);
  });
}

/** Read a reply aloud on the phone itself (free, offline voices). */
export function speak(text: string) {
  if (!("speechSynthesis" in window)) return;
  const clean = text.replace(/```[\s\S]*?```/g, " code on screen ").replace(/https?:\/\/\S+/g, "link").replace(/[*_#`>|]/g, "");
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(clean.slice(0, 600));
  const v = speechSynthesis.getVoices().find((x) => /en-GB/i.test(x.lang) && /male|daniel|arthur|george/i.test(x.name))
    ?? speechSynthesis.getVoices().find((x) => /en-GB/i.test(x.lang));
  if (v) u.voice = v;
  u.rate = 1.05;
  speechSynthesis.speak(u);
}
