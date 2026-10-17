import { useEffect, useRef } from "react";
import { levels } from "../lib/store";

// The ORION mark: the Orion constellation, drawn in the theme's ink colour.
// It draws itself in on first paint, twinkles when idle, sends a pulse along
// the figure while ORION thinks or works, and breathes with the audio level
// while it listens or speaks.

type Star = { x: number; y: number; r: number };
// Normalised 0..1 positions, roughly true to the sky (north up).
const STARS: Record<string, Star> = {
  meissa: { x: 0.49, y: 0.1, r: 0.55 },
  betelgeuse: { x: 0.27, y: 0.22, r: 1 },
  bellatrix: { x: 0.71, y: 0.27, r: 0.78 },
  alnitak: { x: 0.39, y: 0.53, r: 0.72 },
  alnilam: { x: 0.5, y: 0.5, r: 0.78 },
  mintaka: { x: 0.61, y: 0.465, r: 0.7 },
  saiph: { x: 0.33, y: 0.86, r: 0.68 },
  rigel: { x: 0.74, y: 0.82, r: 1 },
};
const LINES: [string, string][] = [
  ["meissa", "betelgeuse"], ["meissa", "bellatrix"], ["betelgeuse", "bellatrix"],
  ["betelgeuse", "alnitak"], ["bellatrix", "mintaka"],
  ["alnitak", "alnilam"], ["alnilam", "mintaka"],
  ["alnitak", "saiph"], ["mintaka", "rigel"],
];
// Order the pulse travels in: head, shoulders, belt, feet.
const PATH = ["meissa", "betelgeuse", "bellatrix", "alnitak", "alnilam", "mintaka", "saiph", "rigel"];
const BUSY = new Set(["thinking", "working", "connecting"]);
const AUDIO = new Set(["listening", "speaking"]);

function ink(): [number, number, number] {
  const v = getComputedStyle(document.documentElement).getPropertyValue("--text").trim();
  const m = /^#?([0-9a-f]{6})$/i.exec(v);
  if (!m) return document.documentElement.dataset.theme === "light" ? [20, 20, 20] : [237, 237, 237];
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function Logo({ size = 32, state = "standby", interactive = false, onClick, ring = false, label }: {
  size?: number; state?: string; interactive?: boolean; onClick?: () => void; ring?: boolean; label?: string;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef(state);
  stateRef.current = state;
  // With reduced motion the loop stops once drawn, so a state change has to redraw it.
  const reduceKey = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches ? state : "";

  useEffect(() => {
    const canvas = ref.current!;
    const ctx = canvas.getContext("2d")!;
    const dpr = Math.min(window.devicePixelRatio || 1, 3);
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let color = ink();
    const theme = new MutationObserver(() => { color = ink(); if (reduce) raf = requestAnimationFrame(draw); });
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const onScheme = () => { color = ink(); if (reduce) raf = requestAnimationFrame(draw); };
    mq.addEventListener("change", onScheme);

    const start = performance.now();
    let raf = 0;
    let smooth = 0;
    let busyMix = 0;
    let errMix = 0;
    // Small marks get a bigger star size relative to the box so they stay legible.
    const pad = size * (ring ? 0.2 : 0.1);
    const span = size - pad * 2;
    const unit = Math.max(1.05, size * (size < 48 ? 0.05 : 0.04));
    // Widen the figure a little so the shoulders and feet read at small sizes.
    const pos = (s: Star) => [pad + (0.5 + (s.x - 0.5) * 1.22) * span, pad + s.y * span] as const;

    const draw = (now: number) => {
      const t = (now - start) / 1000;
      const st = stateRef.current;
      const busy = BUSY.has(st);
      busyMix += ((busy ? 1 : 0) - busyMix) * 0.06;
      errMix += ((st === "error" ? 1 : 0) - errMix) * 0.06;
      const lvl = st === "speaking" ? levels.out : st === "listening" ? levels.mic : 0;
      smooth += (lvl - smooth) * 0.2;
      const audio = AUDIO.has(st);
      const [r0, g0, b0] = color;
      const [r, g, b] = [r0 + (229 - r0) * errMix, g0 + (72 - g0) * errMix, b0 + (77 - b0) * errMix].map(Math.round);
      const rgba = (a: number) => `rgba(${r},${g},${b},${Math.max(0, Math.min(1, a))})`;
      // Draw-in: lines trace over the first 1.1s, stars fade in one by one.
      const intro = reduce ? 1 : Math.min(1, t / 1.1);
      const ease = 1 - Math.pow(1 - intro, 3);
      // A pulse that walks along PATH while busy (one lap every 1.6s).
      const lap = (t / 1.6) % 1;

      ctx.clearRect(0, 0, size, size);

      if (ring) {
        ctx.lineWidth = Math.max(1, size * 0.02);
        ctx.strokeStyle = rgba(0.22 + busyMix * 0.2);
        ctx.beginPath();
        ctx.arc(size / 2, size / 2, size / 2 - ctx.lineWidth, 0, Math.PI * 2);
        ctx.stroke();
        if (busyMix > 0.01 && !reduce) {
          ctx.strokeStyle = rgba(0.85 * busyMix);
          ctx.beginPath();
          const a = t * 3.2;
          ctx.arc(size / 2, size / 2, size / 2 - ctx.lineWidth, a, a + 1.1);
          ctx.stroke();
        }
      }

      // Figure lines
      ctx.lineCap = "round";
      ctx.lineWidth = Math.max(0.75, size * 0.014);
      LINES.forEach(([a, bb], i) => {
        const local = Math.max(0, Math.min(1, ease * LINES.length - i * 0.6));
        if (local <= 0) return;
        const [x1, y1] = pos(STARS[a]);
        const [x2, y2] = pos(STARS[bb]);
        const shimmer = busyMix * (0.25 + 0.25 * Math.sin(t * 5 - i * 0.7));
        ctx.strokeStyle = rgba((size < 48 ? 0.4 : 0.28) + shimmer + (audio ? smooth * 0.4 : 0));
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(x1 + (x2 - x1) * local, y1 + (y2 - y1) * local);
        ctx.stroke();
      });

      // Stars
      PATH.forEach((name, i) => {
        const s = STARS[name];
        const appear = Math.max(0, Math.min(1, intro * 1.6 - i * 0.08));
        if (appear <= 0) return;
        const [x, y] = pos(s);
        const twinkle = reduce ? 0 : Math.sin(t * 1.3 + i * 2.1) * 0.12;
        // Distance from the travelling pulse, wrapped around the path.
        const d = Math.abs(((lap * PATH.length - i) % PATH.length + PATH.length) % PATH.length);
        const hit = Math.max(0, 1 - Math.min(d, PATH.length - d) / 1.2) * busyMix;
        const belt = name === "alnitak" || name === "alnilam" || name === "mintaka";
        const boost = 1 + hit * 0.7 + (audio ? smooth * (belt ? 1.1 : 0.6) : 0) + twinkle;
        const rad = unit * s.r * boost * appear;
        if (size >= 40 || hit > 0.2 || audio) {
          const glow = ctx.createRadialGradient(x, y, 0, x, y, rad * 4);
          // Glow reads as a halo on dark and a smudge on light, so keep it faint on light.
          const halo = r + g + b > 384 ? 1 : 0.45;
          glow.addColorStop(0, rgba(halo * (0.28 * appear + hit * 0.35 + (audio ? smooth * 0.4 : 0))));
          glow.addColorStop(1, rgba(0));
          ctx.fillStyle = glow;
          ctx.beginPath();
          ctx.arc(x, y, rad * 4, 0, Math.PI * 2);
          ctx.fill();
        }
        ctx.fillStyle = rgba(0.95 * appear);
        ctx.beginPath();
        ctx.arc(x, y, rad, 0, Math.PI * 2);
        ctx.fill();
      });

      // Reduced motion: draw one still frame per state.
      if (reduce) return;
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => { cancelAnimationFrame(raf); theme.disconnect(); mq.removeEventListener("change", onScheme); };
  }, [size, ring, reduceKey]);

  return (
    <canvas
      ref={ref}
      className="logo"
      onClick={onClick}
      style={{ width: size, height: size, cursor: interactive ? "pointer" : "default", display: "block", flex: "none" }}
      aria-label={label ?? `ORION, ${state}`}
      role={interactive ? "button" : "img"}
      tabIndex={interactive ? 0 : undefined}
      onKeyDown={interactive ? (e) => (e.key === "Enter" || e.key === " ") && onClick?.() : undefined}
    />
  );
}
