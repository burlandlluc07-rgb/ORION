import { useEffect, useRef } from "react";
import { levels } from "../lib/store";

// Monochrome: brightness carries the state; only errors get colour.
const STATE_COLOR: Record<string, [number, number, number]> = {
  listening: [255, 255, 255],
  speaking: [255, 255, 255],
  thinking: [200, 200, 200],
  working: [225, 225, 225],
  connecting: [140, 140, 140],
  error: [229, 72, 77],
};
const IDLE: [number, number, number] = [120, 120, 120];

/** The ORION "reactor": concentric segmented rings that react to voice state and audio level. */
export function Orb({ size = 160, state = "standby", interactive = false, onClick }: {
  size?: number; state?: string; interactive?: boolean; onClick?: () => void;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef(state);
  stateRef.current = state;

  useEffect(() => {
    const canvas = ref.current!;
    const ctx = canvas.getContext("2d")!;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    ctx.scale(dpr, dpr);
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let raf = 0;
    let t = 0;
    let smooth = 0;
    let color: number[] = [...IDLE];

    const draw = () => {
      const st = stateRef.current;
      const target = STATE_COLOR[st] ?? IDLE;
      color = color.map((c, i) => c + (target[i] - c) * 0.08);
      const active = st in STATE_COLOR && st !== "connecting";
      const lvl = st === "speaking" ? levels.out : st === "listening" ? levels.mic : 0;
      smooth += (lvl - smooth) * 0.25;
      t += reduce ? 0 : active ? 0.016 : 0.006;
      const c = size / 2;
      const [r, g, b] = color.map(Math.round);
      const rgba = (a: number) => `rgba(${r},${g},${b},${a})`;
      ctx.clearRect(0, 0, size, size);

      const glowR = size * (0.28 + smooth * 0.12);
      const grad = ctx.createRadialGradient(c, c, 0, c, c, size * 0.5);
      grad.addColorStop(0, rgba(active ? 0.35 + smooth * 0.4 : 0.12));
      grad.addColorStop(0.45, rgba(active ? 0.08 : 0.03));
      grad.addColorStop(1, rgba(0));
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, size, size);

      // Outer ring
      ctx.lineWidth = Math.max(1.5, size * 0.012);
      ctx.strokeStyle = rgba(0.75);
      ctx.beginPath();
      ctx.arc(c, c, size * 0.42, 0, Math.PI * 2);
      ctx.stroke();

      // Orbiting nodes
      for (let i = 0; i < 3; i++) {
        const a = t * 0.9 + (i * Math.PI * 2) / 3;
        ctx.fillStyle = rgba(0.95);
        ctx.beginPath();
        ctx.arc(c + Math.cos(a) * size * 0.42, c + Math.sin(a) * size * 0.42, size * 0.022, 0, Math.PI * 2);
        ctx.fill();
      }

      // Segmented middle ring (counter-rotating)
      const segs = 12;
      ctx.lineWidth = size * 0.045;
      for (let i = 0; i < segs; i++) {
        const a0 = -t * 0.6 + (i * Math.PI * 2) / segs;
        ctx.strokeStyle = rgba(0.35 + 0.5 * Math.abs(Math.sin(i * 1.7 + t * 2)) * (active ? 1 : 0.4));
        ctx.beginPath();
        ctx.arc(c, c, size * 0.32, a0, a0 + (Math.PI * 2) / segs - 0.12);
        ctx.stroke();
      }

      // Inner audio-reactive ring
      ctx.lineWidth = Math.max(1, size * 0.008);
      ctx.strokeStyle = rgba(0.6);
      ctx.beginPath();
      const pts = 64;
      for (let i = 0; i <= pts; i++) {
        const a = (i / pts) * Math.PI * 2;
        const wob = active ? Math.sin(a * 6 + t * 6) * smooth * size * 0.035 : 0;
        const rr = size * 0.22 + wob;
        const x = c + Math.cos(a) * rr;
        const y = c + Math.sin(a) * rr;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();

      // Core
      const core = ctx.createRadialGradient(c, c, 0, c, c, glowR * 0.6);
      core.addColorStop(0, `rgba(255,255,255,${active ? 1 : 0.7})`);
      core.addColorStop(0.5, rgba(active ? 0.85 : 0.4));
      core.addColorStop(1, rgba(0));
      ctx.fillStyle = core;
      ctx.beginPath();
      ctx.arc(c, c, glowR * 0.6, 0, Math.PI * 2);
      ctx.fill();

      if (st === "connecting" || st === "thinking") {
        ctx.lineWidth = size * 0.012;
        ctx.strokeStyle = rgba(0.9);
        ctx.beginPath();
        ctx.arc(c, c, size * 0.47, t * 4, t * 4 + 1.1);
        ctx.stroke();
      }
      raf = requestAnimationFrame(draw);
    };
    draw();
    return () => cancelAnimationFrame(raf);
  }, [size]);

  return (
    <canvas
      ref={ref}
      onClick={onClick}
      style={{ width: size, height: size, cursor: interactive ? "pointer" : "default", display: "block" }}
      aria-label={`ORION — ${state}`}
      role={interactive ? "button" : "img"}
    />
  );
}
