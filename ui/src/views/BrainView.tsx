import {
  BookOpen, Crosshair, ExternalLink, FolderOpen, MessageSquare, PanelRightClose, PanelRightOpen, Search, Sparkles, Wrench,
  X, ZoomIn, ZoomOut, Ghost,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { BrainGraph, BrainLink, BrainNode, Thought } from "../lib/types";

/* ── The Brain tab ──────────────────────────────────────────────────────────
   ORION's memory drawn as a living graph: ORION at the centre, its notes around it, every remembered fact
   hanging off its note and linked to the people, projects and other facts it relates to. When ORION recalls
   memories, takes a step or replies, a "thought" appears next to the core and the memories it used light up.
   Hand-rolled force layout on a canvas (no extra dependency); the simulation sleeps once it settles. */

type Kind = BrainNode["kind"] | "thought";
interface SimNode extends Omit<BrainNode, "kind"> {
  kind: Kind;
  x: number; y: number; vx: number; vy: number;
  r: number;
  born: number;
  glowUntil: number;
  pinned?: boolean;
  thought?: Thought;
}
interface SimLink { s: SimNode; t: SimNode; kind: BrainLink["kind"] | "think"; weight: number }

const LINK_LEN: Record<SimLink["kind"], number> = { region: 210, note: 85, mention: 170, related: 120, think: 90 };
const LINK_K: Record<SimLink["kind"], number> = { region: 0.08, note: 0.5, mention: 0.06, related: 0.12, think: 0.04 };
const CHARGE: Record<Kind, number> = { core: 6000, note: 4200, memory: 1500, thought: 1200 };
const THOUGHT_NODES = 5;
const THOUGHT_TTL = 90_000;
const GLOW_MS = 9_000;
const reduceMotion = typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;

function radius(n: { kind: Kind; tier?: string; count?: number }) {
  if (n.kind === "core") return 13;
  if (n.kind === "note") return 7.5 + Math.min(n.count ?? 0, 24) * 0.22;
  if (n.kind === "thought") return 4.5;
  return n.tier === "short" ? 4.6 : 5.4;
}

function wrap(text: string, max = 24): string[] {
  const words = text.split(" ");
  const lines: string[] = [];
  let cur = "";
  for (const w of words) {
    if ((cur + " " + w).trim().length > max && cur) {
      lines.push(cur);
      cur = w;
      if (lines.length === 2) break;
    } else cur = (cur + " " + w).trim();
  }
  if (lines.length < 2 && cur) lines.push(cur);
  else if (lines.length === 2 && cur && !lines[1].endsWith("…") && words.join(" ").length > lines.join(" ").length) lines[1] += "…";
  return lines;
}

const THOUGHT_META: Record<string, { label: string; icon: typeof Sparkles }> = {
  recall: { label: "Recalled", icon: Sparkles },
  action: { label: "Doing", icon: Wrench },
  skill: { label: "Skill", icon: BookOpen },
  reply: { label: "Replied", icon: MessageSquare },
  ghost: { label: "Ghost", icon: Ghost },
};

function ago(ts: number) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 5) return "now";
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${Math.round(s / 3600)}h`;
}

interface Palette { bg: string; text: string; text2: string; text3: string; line: string; line2: string; panel: string }
function readPalette(el: HTMLElement): Palette {
  const cs = getComputedStyle(el);
  const v = (n: string) => cs.getPropertyValue(n).trim() || "#888";
  return { bg: v("--bg"), text: v("--text"), text2: v("--text-2"), text3: v("--text-3"), line: v("--line"), line2: v("--line-2"), panel: v("--panel") };
}

/** Force simulation + camera + drawing, kept outside React so 60 fps never re-renders the page. */
class Brain {
  nodes: SimNode[] = [];
  byId = new Map<string, SimNode>();
  links: SimLink[] = [];
  adj = new Map<string, Set<string>>();
  alpha = 1;
  cam = { x: 0, y: 0, k: 0.9 };
  w = 800; h = 600; dpr = 1;
  hover: SimNode | null = null;
  selected: SimNode | null = null;
  focus = new Set<number>();  // memory ids of a thought picked in the list
  query = "";
  matches = new Set<string>();
  touched = false;  // the user moved the camera: stop auto-fitting
  fitted = false;
  dirty = true;
  pal: Palette | null = null;

  setGraph(g: BrainGraph) {
    const now = performance.now();
    const old = this.byId;
    const keep = this.nodes.filter((n) => n.kind === "thought");
    const nodes: SimNode[] = [];
    const byId = new Map<string, SimNode>();
    const angleOf = new Map<string, number>();
    const notes = g.nodes.filter((n) => n.kind === "note");
    notes.forEach((n, i) => angleOf.set(n.id, (i / Math.max(notes.length, 1)) * Math.PI * 2));
    for (const n of g.nodes) {
      const prev = old.get(n.id);
      let x: number, y: number;
      if (prev) ({ x, y } = prev);
      else if (n.kind === "core") { x = 0; y = 0; }
      else if (n.kind === "note") {
        const a = angleOf.get(n.id) ?? Math.random() * 6.28;
        x = Math.cos(a) * 170; y = Math.sin(a) * 170;
      } else {
        const home = byId.get(`note:${n.note}`) ?? old.get(`note:${n.note}`);
        const a = Math.random() * Math.PI * 2;
        x = (home?.x ?? 0) + Math.cos(a) * (40 + Math.random() * 40);
        y = (home?.y ?? 0) + Math.sin(a) * (40 + Math.random() * 40);
      }
      const sn: SimNode = { ...n, x, y, vx: prev?.vx ?? 0, vy: prev?.vy ?? 0, r: radius(n), born: prev?.born ?? (old.size ? now : now - 1000),
        glowUntil: prev?.glowUntil ?? 0, pinned: n.kind === "core" };
      nodes.push(sn);
      byId.set(n.id, sn);
    }
    for (const t of keep) { nodes.push(t); byId.set(t.id, t); }
    this.nodes = nodes;
    this.byId = byId;
    this.links = [];
    for (const l of g.links) {
      const s = byId.get(l.source), t = byId.get(l.target);
      if (s && t) this.links.push({ s, t, kind: l.kind, weight: l.weight });
    }
    for (const t of keep) this.linkThought(t);
    this.rebuildAdj();
    if (this.selected) this.selected = byId.get(this.selected.id) ?? null;
    this.alpha = Math.max(this.alpha, old.size ? 0.45 : 1);
    this.setQuery(this.query);
    this.dirty = true;
  }

  rebuildAdj() {
    this.adj = new Map();
    const add = (a: string, b: string) => {
      if (!this.adj.has(a)) this.adj.set(a, new Set());
      this.adj.get(a)!.add(b);
    };
    for (const l of this.links) { add(l.s.id, l.t.id); add(l.t.id, l.s.id); }
  }

  linkThought(n: SimNode) {
    const core = this.byId.get("core");
    if (core) this.links.push({ s: core, t: n, kind: "think", weight: 1 });
    for (const mid of n.thought?.memories ?? []) {
      const m = this.byId.get(`mem:${mid}`);
      if (m) this.links.push({ s: n, t: m, kind: "think", weight: 1 });
    }
  }

  addThought(t: Thought) {
    const id = `thought:${t.id}`;
    if (this.byId.has(id)) return;
    const now = performance.now();
    const core = this.byId.get("core");
    const a = Math.random() * Math.PI * 2;
    const meta = THOUGHT_META[t.kind];
    const n: SimNode = { id, kind: "thought", label: `${meta?.label ?? "Thought"}: ${t.text}`, group: "thought",
      x: (core?.x ?? 0) + Math.cos(a) * 46, y: (core?.y ?? 0) + Math.sin(a) * 46, vx: 0, vy: 0, r: 4.5, born: now,
      glowUntil: now + GLOW_MS, thought: t };
    this.nodes.push(n);
    this.byId.set(id, n);
    this.linkThought(n);
    for (const mid of t.memories) {
      const m = this.byId.get(`mem:${mid}`);
      if (m) m.glowUntil = now + GLOW_MS;
    }
    if (core) core.glowUntil = now + 2600;
    this.pruneThoughts();
    this.rebuildAdj();
    this.alpha = Math.max(this.alpha, 0.25);
    this.dirty = true;
  }

  pruneThoughts() {
    const now = performance.now();
    const thoughts = this.nodes.filter((n) => n.kind === "thought");
    const drop = new Set(thoughts.filter((n, i) => i < thoughts.length - THOUGHT_NODES || now - n.born > THOUGHT_TTL).map((n) => n.id));
    if (!drop.size) return false;
    this.nodes = this.nodes.filter((n) => !drop.has(n.id));
    this.links = this.links.filter((l) => !drop.has(l.s.id) && !drop.has(l.t.id));
    for (const id of drop) this.byId.delete(id);
    if (this.selected && drop.has(this.selected.id)) this.selected = null;
    this.rebuildAdj();
    return true;
  }

  setQuery(q: string) {
    this.query = q.trim().toLowerCase();
    this.matches = new Set(this.query ? this.nodes.filter((n) => `${n.label} ${n.text ?? ""}`.toLowerCase().includes(this.query)).map((n) => n.id) : []);
    this.dirty = true;
  }

  tick() {
    const a = this.alpha;
    if (a < 0.004) return false;
    const ns = this.nodes;
    // Repulsion (all pairs: a few hundred nodes, and it only runs while the layout is moving).
    for (let i = 0; i < ns.length; i++) {
      const p = ns[i];
      for (let j = i + 1; j < ns.length; j++) {
        const q = ns[j];
        let dx = q.x - p.x, dy = q.y - p.y;
        let d2 = dx * dx + dy * dy;
        if (d2 > 640_000) continue;
        if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
        const d = Math.sqrt(d2);
        const f = (a * Math.sqrt(CHARGE[p.kind] * CHARGE[q.kind])) / d2;
        const minD = p.r + q.r + 14;
        const push = d < minD ? ((minD - d) / d) * 0.5 : 0;
        const fx = (dx / d) * f + dx * push, fy = (dy / d) * f + dy * push;
        p.vx -= fx; p.vy -= fy; q.vx += fx; q.vy += fy;
      }
    }
    // Springs.
    for (const l of this.links) {
      const dx = l.t.x - l.s.x, dy = l.t.y - l.s.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const k = LINK_K[l.kind] * (l.kind === "related" ? 0.6 + l.weight : 1);
      const f = ((d - LINK_LEN[l.kind]) / d) * a * k;
      const ds = this.adj.get(l.s.id)?.size ?? 1, dt = this.adj.get(l.t.id)?.size ?? 1;
      const bias = ds / (ds + dt);
      l.t.vx -= dx * f * bias; l.t.vy -= dy * f * bias;
      l.s.vx += dx * f * (1 - bias); l.s.vy += dy * f * (1 - bias);
    }
    for (const n of ns) {
      if (n.pinned) { n.vx = n.vy = 0; continue; }
      n.vx -= n.x * 0.004 * a; n.vy -= n.y * 0.004 * a;  // gentle pull to the middle
      n.vx *= 0.58; n.vy *= 0.58;
      n.x += n.vx; n.y += n.vy;
    }
    this.alpha += (0 - this.alpha) * 0.018;
    return true;
  }

  toScreen(n: { x: number; y: number }) {
    return { x: n.x * this.cam.k + this.w / 2 + this.cam.x, y: n.y * this.cam.k + this.h / 2 + this.cam.y };
  }
  toWorld(sx: number, sy: number) {
    return { x: (sx - this.w / 2 - this.cam.x) / this.cam.k, y: (sy - this.h / 2 - this.cam.y) / this.cam.k };
  }
  pick(sx: number, sy: number): SimNode | null {
    const p = this.toWorld(sx, sy);
    let best: SimNode | null = null, bd = Infinity;
    for (const n of this.nodes) {
      const d = Math.hypot(n.x - p.x, n.y - p.y);
      const hit = n.r + 6 / this.cam.k;
      if (d < hit && d < bd) { best = n; bd = d; }
    }
    return best;
  }
  fit(animate = false) {
    if (!this.nodes.length) return;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of this.nodes) { x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y); x1 = Math.max(x1, n.x); y1 = Math.max(y1, n.y); }
    const inset = this.w > 760 ? this.rightInset : 0;
    const k = Math.max(0.25, Math.min(1.6, (this.w - inset - 60) / (x1 - x0 + 160), (this.h - 110) / (y1 - y0 + 90)));
    const target = { k, x: -((x0 + x1) / 2) * k - inset / 2, y: -((y0 + y1) / 2) * k + 6 };
    if (animate && !reduceMotion) this.camTarget = target;
    else this.cam = target;
    this.dirty = true;
  }
  camTarget: { x: number; y: number; k: number } | null = null;
  rightInset = 0;  // the thoughts panel covers this much of the right edge
  zoomAt(factor: number, sx = this.w / 2, sy = this.h / 2) {
    const before = this.toWorld(sx, sy);
    this.cam.k = Math.max(0.15, Math.min(4, this.cam.k * factor));
    const after = this.toWorld(sx, sy);
    this.cam.x += (after.x - before.x) * this.cam.k;
    this.cam.y += (after.y - before.y) * this.cam.k;
    this.camTarget = null;
    this.touched = true;
    this.dirty = true;
  }
  centerOn(n: SimNode) {
    const k = Math.max(this.cam.k, 1.1);
    this.camTarget = { k, x: -n.x * k, y: -n.y * k };
    this.touched = true;
    this.dirty = true;
  }

  /** True while something is moving or glowing, so the loop knows whether to keep drawing. */
  animating(now: number) {
    return this.alpha >= 0.004 || !!this.camTarget || this.nodes.some((n) => n.glowUntil > now || now - n.born < 700 || n.kind === "thought");
  }

  draw(ctx: CanvasRenderingContext2D, now: number) {
    const pal = this.pal!;
    const { k } = this.cam;
    if (this.camTarget) {
      const t = this.camTarget;
      this.cam.x += (t.x - this.cam.x) * 0.16; this.cam.y += (t.y - this.cam.y) * 0.16; this.cam.k += (t.k - this.cam.k) * 0.16;
      if (Math.abs(t.k - this.cam.k) < 0.002 && Math.abs(t.x - this.cam.x) < 0.5 && Math.abs(t.y - this.cam.y) < 0.5) { this.cam = t; this.camTarget = null; }
    }
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.w, this.h);
    ctx.save();
    ctx.translate(this.w / 2 + this.cam.x, this.h / 2 + this.cam.y);
    ctx.scale(this.cam.k, this.cam.k);

    const lit = this.hover ?? this.selected;
    const litSet = lit ? new Set([lit.id, ...(this.adj.get(lit.id) ?? [])]) : null;
    const focusSet = this.focus.size ? new Set([...this.focus].map((m) => `mem:${m}`)) : null;
    const dimmed = (n: SimNode) =>
      (litSet && !litSet.has(n.id)) || (focusSet && !focusSet.has(n.id) && n.kind === "memory") || (this.matches.size > 0 && !this.matches.has(n.id) && n.kind !== "core");
    const pulse = reduceMotion ? 0.5 : (Math.sin(now / 260) + 1) / 2;

    // Links.
    ctx.lineCap = "round";
    for (const l of this.links) {
      const on = litSet && litSet.has(l.s.id) && litSet.has(l.t.id) && (l.s === lit || l.t === lit);
      const off = (litSet && !on) || (this.matches.size > 0 && !(this.matches.has(l.s.id) || this.matches.has(l.t.id)));
      if (l.kind === "think") {
        const th = l.s.kind === "thought" ? l.s : l.t;
        const age = now - th.born;
        const fade = Math.max(0, 1 - age / THOUGHT_TTL);
        ctx.strokeStyle = pal.text;
        ctx.globalAlpha = (off ? 0.08 : 0.55) * fade;
        ctx.lineWidth = 1 / k;
        ctx.setLineDash([3 / k, 4 / k]);
        ctx.lineDashOffset = reduceMotion ? 0 : -now / 60 / k;
      } else {
        ctx.setLineDash([]);
        ctx.strokeStyle = on ? pal.text : pal.text3;
        ctx.globalAlpha = off ? 0.07 : on ? 0.9 : l.kind === "region" ? 0.32 : l.kind === "related" ? 0.42 : 0.5;
        ctx.lineWidth = (on ? 1.4 : l.kind === "region" ? 1.1 : 0.85) / Math.max(k, 0.6);
      }
      ctx.beginPath();
      ctx.moveTo(l.s.x, l.s.y);
      ctx.lineTo(l.t.x, l.t.y);
      ctx.stroke();
    }
    ctx.setLineDash([]);

    // Nodes.
    const many = this.nodes.length > 140;
    for (const n of this.nodes) {
      const grow = Math.min(1, (now - n.born) / 600);
      const r = n.r * (reduceMotion ? 1 : 0.4 + 0.6 * (1 - Math.pow(1 - grow, 3)));
      const dim = dimmed(n);
      const glow = n.glowUntil > now ? (n.glowUntil - now) / GLOW_MS : 0;
      const fade = n.kind === "thought" ? Math.max(0.15, 1 - (now - n.born) / THOUGHT_TTL) : 1;
      ctx.globalAlpha = (dim ? 0.18 : 1) * fade;
      if (glow > 0 && !dim) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 4 + pulse * 5, 0, Math.PI * 2);
        ctx.strokeStyle = pal.text;
        ctx.globalAlpha = Math.min(1, glow * 1.4) * 0.5 * fade;
        ctx.lineWidth = 1.2 / Math.max(k, 0.6);
        ctx.stroke();
        ctx.globalAlpha = (dim ? 0.18 : 1) * fade;
      }
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
      if (n.kind === "core") {
        ctx.fillStyle = pal.text;
        ctx.fill();
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 5, 0, Math.PI * 2);
        ctx.strokeStyle = pal.text;
        ctx.lineWidth = 1.2 / Math.max(k, 0.6);
        ctx.globalAlpha *= 0.45;
        ctx.stroke();
        ctx.globalAlpha = dim ? 0.18 : 1;
      } else if (n.kind === "note") {
        ctx.fillStyle = pal.bg;
        ctx.fill();
        ctx.lineWidth = 2 / Math.max(k, 0.7);
        ctx.strokeStyle = pal.text;
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(n.x, n.y, r * 0.42, 0, Math.PI * 2);
        ctx.fillStyle = pal.text;
        ctx.fill();
      } else if (n.kind === "thought") {
        ctx.fillStyle = pal.bg;
        ctx.fill();
        ctx.lineWidth = 1.4 / Math.max(k, 0.7);
        ctx.strokeStyle = pal.text;
        ctx.stroke();
      } else {
        ctx.fillStyle = n.tier === "short" ? pal.text3 : pal.text;
        ctx.fill();
      }
      if (this.selected === n) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 3.5, 0, Math.PI * 2);
        ctx.strokeStyle = pal.text;
        ctx.lineWidth = 1.5 / Math.max(k, 0.6);
        ctx.globalAlpha = 1;
        ctx.stroke();
      }
    }

    // Labels: always for ORION and notes; facts once zoomed in enough (or when lit, glowing or searched).
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    const fs = 10.5 / Math.max(k, 0.55);
    const newestThought = [...this.nodes].reverse().find((n) => n.kind === "thought");
    const rank = (n: SimNode) => (n.kind === "core" ? 0 : n.kind === "note" ? 1 : n === this.selected || n === this.hover ? 2
      : this.matches.has(n.id) || n.glowUntil > now ? 3 : n.kind === "thought" ? 4 : 5);
    const placed: { x0: number; y0: number; x1: number; y1: number }[] = [];
    for (const n of [...this.nodes].sort((a, b) => rank(a) - rank(b))) {
      if (n.kind === "thought" && n !== newestThought && n !== this.hover && n !== this.selected) continue;
      const dim = dimmed(n);
      const important = n.kind === "core" || n.kind === "note" || (litSet?.has(n.id) ?? false) || this.matches.has(n.id) || n.glowUntil > now || n === this.selected;
      const zoomOk = n.kind === "thought" ? k > 0.7 : k >= (many ? 1.15 : 0.7);
      if (!important && !zoomOk) continue;
      if (dim && !important) continue;
      const fade = n.kind === "thought" ? Math.max(0.15, 1 - (now - n.born) / THOUGHT_TTL) : 1;
      ctx.globalAlpha = (dim ? 0.25 : n.kind === "memory" || n.kind === "thought" ? 0.92 : 1) * fade;
      const weight = n.kind === "core" || n.kind === "note" ? 600 : 400;
      const size = n.kind === "core" ? fs * 1.25 : n.kind === "note" ? fs * 1.08 : fs;
      ctx.font = `${weight} ${size}px ${getFont()}`;
      ctx.fillStyle = n.kind === "memory" || n.kind === "thought" ? pal.text2 : pal.text;
      const lines = n.kind === "core" ? [n.label] : wrap(n.label, n.kind === "thought" ? 30 : 24);
      const top = n.y + n.r + 5 / Math.max(k, 0.55);
      const wid = Math.max(...lines.map((ln) => ctx.measureText(ln).width));
      const box = { x0: n.x - wid / 2, y0: top, x1: n.x + wid / 2, y1: top + lines.length * size * 1.2 };
      if (rank(n) >= 3 && placed.some((b) => b.x0 < box.x1 && box.x0 < b.x1 && b.y0 < box.y1 && box.y0 < b.y1)) continue;
      placed.push(box);
      lines.forEach((ln, i) => ctx.fillText(ln, n.x, n.y + n.r + 5 / Math.max(k, 0.55) + i * size * 1.2));
    }
    ctx.restore();
    ctx.globalAlpha = 1;
    this.dirty = false;
  }
}

let fontCache = "";
function getFont() {
  if (!fontCache) fontCache = getComputedStyle(document.documentElement).getPropertyValue("--font").trim() || "system-ui, sans-serif";
  return fontCache;
}

export function BrainView() {
  const bump = useStore((s) => s.bump["memory.changed"] ?? 0);
  const liveThoughts = useStore((s) => s.thoughts);
  const connected = useStore((s) => s.connected);
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const brain = useRef(new Brain()).current;
  const [graph, setGraph] = useState<BrainGraph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<SimNode | null>(null);
  const [q, setQ] = useState("");
  const [focusId, setFocusId] = useState<string | null>(null);
  const [panel, setPanel] = useState(() => localStorage.getItem("orion.brain.panel") !== "0");
  const [obsidian, setObsidian] = useState(false);
  const seen = useRef(new Set<string>());

  // Load (and reload whenever a memory is added, edited in Obsidian, reviewed or forgotten).
  useEffect(() => {
    let live = true;
    api.get<BrainGraph>("/api/brain/graph").then((g) => {
      if (!live) return;
      brain.setGraph(g);
      setGraph(g);
      setError(null);
      for (const t of g.thoughts) {
        if (!seen.current.has(t.id)) { seen.current.add(t.id); if (Date.now() / 1000 - t.ts < THOUGHT_TTL / 1000) brain.addThought(t); }
      }
    }).catch((e) => live && setError((e as Error).message));
    return () => { live = false; };
  }, [bump, brain]);
  useEffect(() => { api.get<{ obsidian: boolean }>("/api/memory/vault").then((v) => setObsidian(!!v.obsidian)).catch(() => {}); }, []);

  // Live thoughts from the engine.
  useEffect(() => {
    for (const t of liveThoughts) {
      if (seen.current.has(t.id)) continue;
      seen.current.add(t.id);
      brain.addThought(t);
    }
  }, [liveThoughts, brain]);

  const thoughts = useMemo(() => {
    const all = new Map<string, Thought>();
    for (const t of graph?.thoughts ?? []) all.set(t.id, t);
    for (const t of liveThoughts) all.set(t.id, t);
    return [...all.values()].sort((a, b) => b.ts - a.ts).slice(0, 30);
  }, [graph, liveThoughts]);

  // Canvas size, render loop, pointer handling.
  useEffect(() => {
    const wrap = wrapRef.current!, canvas = canvasRef.current!;
    const ctx = canvas.getContext("2d")!;
    const resize = () => {
      const r = wrap.getBoundingClientRect();
      brain.w = r.width; brain.h = r.height; brain.dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(r.width * brain.dpr); canvas.height = Math.round(r.height * brain.dpr);
      canvas.style.width = `${r.width}px`; canvas.style.height = `${r.height}px`;
      brain.pal = readPalette(wrap);
      brain.dirty = true;
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);
    const themeWatch = new MutationObserver(() => { brain.pal = readPalette(wrap); brain.dirty = true; });
    themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

    let raf = 0, lastPrune = 0;
    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      if (document.hidden) return;
      let moved = false;
      for (let i = 0; i < 2; i++) moved = brain.tick() || moved;
      if (!brain.touched && !brain.fitted && brain.alpha < 0.12 && brain.nodes.length > 1) { brain.fitted = true; brain.fit(true); }
      else if (!brain.touched && !brain.fitted && moved) brain.fit();
      if (now - lastPrune > 2000) { lastPrune = now; if (brain.pruneThoughts()) brain.dirty = true; }
      if (moved || brain.dirty || brain.animating(now)) brain.draw(ctx, now);
    };
    raf = requestAnimationFrame(loop);

    let drag: { node: SimNode | null; x: number; y: number; moved: boolean } | null = null;
    const pos = (e: PointerEvent | WheelEvent | MouseEvent) => {
      const r = canvas.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };
    const down = (e: PointerEvent) => {
      const p = pos(e);
      const node = brain.pick(p.x, p.y);
      drag = { node, x: p.x, y: p.y, moved: false };
      canvas.setPointerCapture(e.pointerId);
      if (node && node.kind !== "core") node.pinned = true;
    };
    const move = (e: PointerEvent) => {
      const p = pos(e);
      if (drag) {
        const dx = p.x - drag.x, dy = p.y - drag.y;
        if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
        if (drag.node && drag.node.kind !== "core") {
          const w = brain.toWorld(p.x, p.y);
          drag.node.x = w.x; drag.node.y = w.y;
          brain.alpha = Math.max(brain.alpha, 0.18);
        } else if (drag.moved) {
          brain.cam.x += dx; brain.cam.y += dy; brain.camTarget = null; brain.touched = true;
        }
        drag.x = p.x; drag.y = p.y;
        brain.dirty = true;
        return;
      }
      const h = brain.pick(p.x, p.y);
      if (h !== brain.hover) { brain.hover = h; brain.dirty = true; canvas.style.cursor = h ? "pointer" : "grab"; }
    };
    const up = (e: PointerEvent) => {
      if (!drag) return;
      if (drag.node && drag.node.kind !== "core") drag.node.pinned = false;
      if (!drag.moved) {
        brain.selected = drag.node;
        brain.focus = new Set();
        setFocusId(null);
        setSelected(drag.node);
        brain.dirty = true;
      }
      drag = null;
      try { canvas.releasePointerCapture(e.pointerId); } catch { /* already released */ }
    };
    const leave = () => { if (brain.hover) { brain.hover = null; brain.dirty = true; } };
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      const p = pos(e);
      brain.zoomAt(Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0018)), p.x, p.y);
    };
    const dbl = (e: MouseEvent) => {
      const p = pos(e);
      const n = brain.pick(p.x, p.y);
      if (n) brain.centerOn(n); else brain.fit(true);
    };
    canvas.addEventListener("pointerdown", down);
    canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerup", up);
    canvas.addEventListener("pointerleave", leave);
    canvas.addEventListener("wheel", wheel, { passive: false });
    canvas.addEventListener("dblclick", dbl);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      themeWatch.disconnect();
      canvas.removeEventListener("pointerdown", down);
      canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("pointerup", up);
      canvas.removeEventListener("pointerleave", leave);
      canvas.removeEventListener("wheel", wheel);
      canvas.removeEventListener("dblclick", dbl);
    };
  }, [brain]);

  useEffect(() => { brain.setQuery(q); }, [q, brain]);
  useEffect(() => { brain.rightInset = panel ? 296 : 0; if (!brain.touched) brain.fit(true); }, [panel, brain]);
  useEffect(() => { try { localStorage.setItem("orion.brain.panel", panel ? "1" : "0"); } catch { /* private mode */ } }, [panel]);

  const pickThought = (t: Thought) => {
    if (focusId === t.id) { setFocusId(null); brain.focus = new Set(); brain.dirty = true; return; }
    setFocusId(t.id);
    brain.focus = new Set(t.memories);
    const now = performance.now();
    for (const m of t.memories) { const n = brain.byId.get(`mem:${m}`); if (n) n.glowUntil = now + GLOW_MS; }
    brain.selected = null;
    setSelected(null);
    brain.dirty = true;
  };
  const closeDetail = () => { brain.selected = null; setSelected(null); brain.dirty = true; };
  const openNote = (note?: string) => api.post("/api/memory/open", { obsidian, note });
  const searchEnter = () => {
    const first = brain.nodes.find((n) => brain.matches.has(n.id));
    if (first) { brain.centerOn(first); brain.selected = first; setSelected(first); }
  };

  const st = graph?.stats;
  const neighbours = selected ? [...(brain.adj.get(selected.id) ?? [])].map((id) => brain.byId.get(id)).filter((n): n is SimNode => !!n && n.kind === "memory").slice(0, 6) : [];

  return (
    <div className="page brain-page">
      <div className="brain-head">
        <div className="grow">
          <h2>Brain</h2>
          <p>A live map of everything ORION remembers. Facts hang off their note and link to the people, projects and other facts they relate to. It lights up as ORION recalls memories and works.</p>
        </div>
        <div className="brain-stats">
          {st && <>
            <span><b>{st.memories}</b> memories</span>
            <span><b>{st.notes}</b> notes</span>
            <span><b>{st.links}</b> links</span>
            <span className="brain-live"><span className={`dot ${connected ? "ok pulse" : "warn"}`} /> {connected ? "Live" : "Reconnecting"}</span>
          </>}
        </div>
      </div>

      <div className="brain-stage" ref={wrapRef}>
        <canvas ref={canvasRef} className="brain-canvas" aria-label="Graph of ORION's memories" role="img" />

        <div className="brain-tools">
          <label className="brain-search">
            <Search size={14} />
            <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && searchEnter()} placeholder="Find a memory" aria-label="Find a memory" />
            {q && <button className="icon-btn sm" onClick={() => setQ("")} aria-label="Clear search"><X size={13} /></button>}
          </label>
          <div className="brain-zoom">
            <button className="icon-btn sm" onClick={() => brain.zoomAt(1.25)} title="Zoom in" aria-label="Zoom in"><ZoomIn size={15} /></button>
            <button className="icon-btn sm" onClick={() => brain.zoomAt(0.8)} title="Zoom out" aria-label="Zoom out"><ZoomOut size={15} /></button>
            <button className="icon-btn sm" onClick={() => { brain.touched = false; brain.fit(true); }} title="Fit to screen" aria-label="Fit to screen"><Crosshair size={15} /></button>
            <button className={`icon-btn sm ${panel ? "on" : ""}`} onClick={() => setPanel(!panel)} title={panel ? "Hide thoughts" : "Show thoughts"} aria-label="Toggle thoughts">
              {panel ? <PanelRightClose size={15} /> : <PanelRightOpen size={15} />}
            </button>
          </div>
        </div>

        <div className="brain-legend" aria-hidden>
          <span><i className="lg core" /> ORION</span>
          <span><i className="lg note" /> Note</span>
          <span><i className="lg mem" /> Memory</span>
          <span><i className="lg short" /> This week</span>
          <span><i className="lg thought" /> Thought</span>
        </div>

        {graph && graph.stats.memories === 0 && (
          <div className="brain-empty">
            <b>ORION's brain is still empty.</b>
            <span>Tell it something to remember, like "remember that I play padel on Thursdays", and it appears here.</span>
          </div>
        )}
        {error && <div className="brain-empty"><b>Couldn't load the brain.</b><span>{error}</span></div>}

        {panel && (
          <aside className="brain-thoughts" aria-label="Latest thoughts">
            <div className="bt-head"><Sparkles size={14} /> Latest thoughts</div>
            {thoughts.length === 0 && <div className="bt-empty">Nothing yet. Ask ORION something and its thoughts show up here as they happen.</div>}
            <div className="bt-list">
              {thoughts.map((t) => {
                const meta = THOUGHT_META[t.kind] ?? { label: "Thought", icon: Sparkles };
                const Icon = meta.icon;
                return (
                  <button key={t.id} className={`bt-item ${focusId === t.id ? "on" : ""}`} onClick={() => pickThought(t)}
                    title={t.memories.length ? "Show the memories this used" : undefined}>
                    <Icon size={13} className="bt-ic" />
                    <span className="bt-body">
                      <span className="bt-top"><b>{meta.label}</b>{t.memories.length > 0 && <span className="bt-n">{t.memories.length} memor{t.memories.length === 1 ? "y" : "ies"}</span>}<span className="bt-ago">{ago(t.ts)}</span></span>
                      <span className="bt-text">{t.text}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          </aside>
        )}

        {selected && selected.kind !== "core" && (
          <div className="brain-detail">
            <div className="bd-head">
              <span className="bd-kind">{selected.kind === "note" ? "Note" : selected.kind === "thought" ? "Thought" : selected.tier === "short" ? "This week" : "Memory"}</span>
              <button className="icon-btn sm" onClick={closeDetail} aria-label="Close"><X size={14} /></button>
            </div>
            <div className="bd-text">{selected.kind === "memory" ? selected.text : selected.kind === "thought" ? selected.thought?.text : selected.label}</div>
            <div className="bd-meta">
              {selected.kind === "memory" && <>In <b>{brain.byId.get(`note:${selected.note}`)?.label ?? selected.note}</b>{selected.created ? <> · {new Date(selected.created * 1000).toLocaleDateString()}</> : null}</>}
              {selected.kind === "note" && <>{selected.count} memor{selected.count === 1 ? "y" : "ies"}</>}
            </div>
            {neighbours.length > 0 && selected.kind === "memory" && (
              <div className="bd-rel">
                <div className="bd-sub">Linked to</div>
                {neighbours.map((n) => (
                  <button key={n.id} className="bd-link" onClick={() => { brain.selected = n; brain.centerOn(n); setSelected(n); }}>{n.label}</button>
                ))}
              </div>
            )}
            {(selected.kind === "memory" || selected.kind === "note") && (
              <div className="row" style={{ gap: 6, marginTop: 4 }}>
                <button className="btn sm" onClick={() => openNote(selected.note)}>
                  {obsidian ? <><ExternalLink size={13} /> Open in Obsidian</> : <><FolderOpen size={13} /> Open folder</>}
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
