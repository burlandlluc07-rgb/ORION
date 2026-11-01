import { useEffect, useRef } from "react";
import { levels } from "../lib/store";

/**
 * ORION's avatar: a small star spirit. A see-through ghost body, two four-point star eyes, and Orion's belt
 * (three stars) across his brow, all in the theme's ink, so black, white and grey only.
 *
 * Moods: idle (floats, blinks), listening (eyes wide, rings ripple out), thinking (looks up, belt stars orbit),
 * working (the ghost "phase" shimmer he wears while he uses his ghost mouse), speaking (mouth moves with the
 * audio level), sleeping (eyes shut, dim, tiny stars drift up), alert (eyes big, glow pulses for an approval),
 * happy (eyes become arcs).
 */
export type Mood = "idle" | "listening" | "thinking" | "working" | "speaking" | "sleeping" | "alert" | "happy";

/** Maps ORION's voice/engine state to a mood. */
export function moodFor(state?: string, busy = false, approvals = 0): Mood {
  if (approvals > 0) return "alert";
  switch (state) {
    case "listening": return "listening";
    case "speaking": return "speaking";
    case "thinking": case "connecting": return "thinking";
    case "working": return "working";
    case "off": case "sleeping": return busy ? "working" : "idle";
  }
  return busy ? "working" : "idle";
}

function ink(): [number, number, number] {
  const v = getComputedStyle(document.documentElement).getPropertyValue("--text").trim();
  const m = /^#?([0-9a-f]{6})$/i.exec(v);
  if (!m) return document.documentElement.dataset.theme === "light" ? [20, 20, 20] : [237, 237, 237];
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function star(ctx: CanvasRenderingContext2D, x: number, y: number, r: number, squash = 1) {
  // Four-point sparkle; squash < 1 closes it like an eyelid.
  const k = r * 0.28;
  ctx.beginPath();
  ctx.moveTo(x, y - r * squash);
  ctx.quadraticCurveTo(x + k, y - k * squash, x + r, y);
  ctx.quadraticCurveTo(x + k, y + k * squash, x, y + r * squash);
  ctx.quadraticCurveTo(x - k, y + k * squash, x - r, y);
  ctx.quadraticCurveTo(x - k, y - k * squash, x, y - r * squash);
  ctx.fill();
}

export function Avatar({ size = 64, mood = "idle", title }: { size?: number; mood?: Mood; title?: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const moodRef = useRef(mood);
  moodRef.current = mood;

  useEffect(() => {
    const canvas = ref.current!;
    const ctx = canvas.getContext("2d")!;
    const dpr = Math.min(window.devicePixelRatio || 1, 3);
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const reduce = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
    let color = ink();
    const theme = new MutationObserver(() => { color = ink(); });
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    let raf = 0;
    let nextBlink = performance.now() + 2500;
    let blinkStart = -1;
    const t0 = performance.now();

    const draw = (now: number) => {
      const m = moodRef.current;
      const t = reduce ? 0 : (now - t0) / 1000;
      const [r, g, b] = color;
      const c = (a: number) => `rgba(${r},${g},${b},${a})`;
      const S = size;
      ctx.clearRect(0, 0, S, S);

      const bob = Math.sin(t * (m === "sleeping" ? 0.8 : 1.6)) * S * 0.025;
      const cx = S / 2;
      const R = S * 0.27; // head radius
      const cy = S * 0.42 + bob;
      const dim = m === "sleeping" ? 0.55 : 1;

      // Listening: ripples; alert: a breathing glow.
      if (m === "listening") {
        for (let i = 0; i < 2; i++) {
          const p = ((t * 0.8 + i * 0.5) % 1);
          ctx.strokeStyle = c(0.35 * (1 - p));
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.arc(cx, cy, R * (1.1 + p * 0.75), 0, Math.PI * 2);
          ctx.stroke();
        }
      }
      const glowA = m === "alert" ? 0.22 + 0.14 * Math.sin(t * 5) : m === "speaking" ? 0.16 + levels.out * 0.25 : 0.12;
      const glow = ctx.createRadialGradient(cx, cy, R * 0.2, cx, cy, R * 1.9);
      glow.addColorStop(0, c(glowA * dim));
      glow.addColorStop(1, c(0));
      ctx.fillStyle = glow;
      ctx.fillRect(0, 0, S, S);

      // Body: round head flowing into a wavy hem, see-through like his ghost windows.
      const hemY = cy + R * 1.45;
      const phase = m === "working";
      const slices = phase ? 7 : 1;
      for (let i = 0; i < slices; i++) {
        // While working, the body is drawn in horizontal bands that slide a little: the "phase" look.
        const y0 = cy - R - 2 + ((hemY - cy + R + 6) / slices) * i;
        const y1 = cy - R - 2 + ((hemY - cy + R + 6) / slices) * (i + 1);
        const dx = phase ? Math.sin(t * 9 + i * 1.7) * S * 0.018 : 0;
        ctx.save();
        if (phase) {
          ctx.beginPath();
          ctx.rect(0, y0, S, y1 - y0 + 0.5);
          ctx.clip();
        }
        ctx.translate(dx, 0);
        ctx.beginPath();
        ctx.arc(cx, cy, R, Math.PI, 0);
        ctx.lineTo(cx + R, hemY - R * 0.15);
        const waves = 3;
        const w = (R * 2) / waves;
        for (let k = 0; k < waves; k++) {
          const x0 = cx + R - w * k;
          const lift = Math.sin(t * 2.4 + k * 1.3) * R * 0.08;
          ctx.quadraticCurveTo(x0 - w * 0.25, hemY + R * 0.12 + lift, x0 - w * 0.5, hemY - R * 0.05);
          ctx.quadraticCurveTo(x0 - w * 0.75, hemY - R * 0.22 - lift, x0 - w, hemY - R * 0.12);
        }
        ctx.closePath();
        const body = ctx.createLinearGradient(0, cy - R, 0, hemY + R * 0.2);
        body.addColorStop(0, c((phase ? 0.2 : 0.24) * dim));
        body.addColorStop(1, c(0.02));
        ctx.fillStyle = body;
        ctx.fill();
        ctx.strokeStyle = c((phase ? 0.45 : 0.6) * dim);
        ctx.lineWidth = Math.max(1, S / 64);
        ctx.stroke();
        ctx.restore();
      }

      // Orion's belt across the brow; the stars orbit while he thinks.
      ctx.fillStyle = c(0.9 * dim);
      for (let i = -1; i <= 1; i++) {
        let bx = cx + i * R * 0.34;
        let by = cy - R * 0.55 - i * R * 0.06;
        if (m === "thinking") {
          const a = t * 2.2 + (i + 1) * ((Math.PI * 2) / 3);
          bx = cx + Math.cos(a) * R * 1.25;
          by = cy - R * 0.2 + Math.sin(a) * R * 0.35;
        }
        star(ctx, bx, by, S * 0.03 * (i === 0 ? 1.15 : 1));
      }

      // Eyes.
      if (now >= nextBlink && blinkStart < 0 && m !== "sleeping") blinkStart = now;
      let open = 1;
      if (blinkStart >= 0) {
        const p = (now - blinkStart) / 160;
        open = p < 1 ? Math.abs(1 - p * 2) : 1;
        if (p >= 1) { blinkStart = -1; nextBlink = now + 2600 + Math.random() * 3200; }
      }
      const look = m === "thinking" ? -R * 0.12 : 0;
      const ex = R * 0.42;
      const ey = cy + R * 0.02 + look;
      const er = S * (m === "alert" || m === "listening" ? 0.075 : m === "working" ? 0.055 : 0.065);
      ctx.fillStyle = c(dim);
      ctx.strokeStyle = c(0.9 * dim);
      ctx.lineWidth = Math.max(1.2, S / 40);
      ctx.lineCap = "round";
      for (const side of [-1, 1]) {
        const x = cx + side * ex;
        if (m === "sleeping" || m === "happy") {
          ctx.beginPath();
          if (m === "sleeping") ctx.arc(x, ey - er * 0.2, er * 0.7, 0.15 * Math.PI, 0.85 * Math.PI);
          else ctx.arc(x, ey + er * 0.4, er * 0.7, 1.15 * Math.PI, 1.85 * Math.PI);
          ctx.stroke();
        } else {
          star(ctx, x, ey, er, Math.max(0.08, open * (m === "working" ? 0.75 : 1)));
        }
      }

      // Mouth: a small line, opens with his voice.
      const mo = m === "speaking" ? Math.min(1, 0.25 + levels.out * 2.2 + Math.abs(Math.sin(t * 11)) * 0.25) : 0;
      const my = cy + R * 0.42;
      ctx.beginPath();
      if (mo > 0.05) ctx.ellipse(cx, my, R * 0.16, R * 0.12 * mo, 0, 0, Math.PI * 2);
      else ctx.moveTo(cx - R * 0.1, my), ctx.quadraticCurveTo(cx, my + R * (m === "happy" ? 0.12 : 0.05), cx + R * 0.1, my);
      if (mo > 0.05) { ctx.fillStyle = c(0.85 * dim); ctx.fill(); } else ctx.stroke();

      // Sleeping: tiny stars drift up and fade.
      if (m === "sleeping") {
        for (let i = 0; i < 3; i++) {
          const p = (t * 0.3 + i / 3) % 1;
          ctx.fillStyle = c(0.6 * (1 - p));
          star(ctx, cx + R * (0.9 + p * 0.4), cy - R * (0.6 + p * 1.3), S * 0.022 * (1 - p * 0.4));
        }
      }
      if (!reduce) raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => { cancelAnimationFrame(raf); theme.disconnect(); };
  }, [size]);

  return <canvas ref={ref} className="orion-avatar" role="img" aria-label={title ?? `ORION, ${mood}`}
    style={{ width: size, height: size, display: "block" }} />;
}
