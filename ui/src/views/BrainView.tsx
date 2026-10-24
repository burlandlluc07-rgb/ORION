import { BrainCircuit, Crosshair, Minus, Plus, Search, X, Zap } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import { eventTaps, levels, useStore } from "../lib/store";
import type { BrainMap } from "../lib/types";

const KIND: Record<string, string> = { F: "File", c: "Class", m: "Method", f: "Function", s: "Definition" };
const FEED_MAX = 7;

interface Signal {
  id: number;
  at: number;
  text: string;
  node: number;
}

type Pulse = { a: number; b: number; t0: number; dur: number; s: number; hops: number };

/** Draws the map on a canvas and animates signals. Lives outside React: it redraws every frame. */
class BrainCanvas {
  ctx: CanvasRenderingContext2D;
  layer = document.createElement("canvas");
  w = 0;
  h = 0;
  dpr = 1;
  scale = 1;
  fitScale = 1;
  ox = 0;
  oy = 0;
  deg: Int32Array;
  adj: number[][];
  energy: Float32Array;
  heat: Float32Array;
  centroids: [number, number][];
  pulses: Pulse[] = [];
  emphasis: Set<number> | null = null;
  focusRegion: number | null = null;
  hover = -1;
  selected = -1;
  dirty = true;
  colors = { text: "#ededed", text2: "#a3a3a3", text3: "#6f6f6f", accent: "#f5f5f5", bg: "#141414", font: "sans-serif" };
  reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  nextSpark = 0;
  last = 0;
  raf = 0;
  micNode = -1;
  /** Space the header takes at the top, so the whole brain fits below it. */
  insetTop = 0;
  listening = () => false;

  constructor(public canvas: HTMLCanvasElement, public map: BrainMap) {
    this.ctx = canvas.getContext("2d")!;
    const n = map.nodes.length;
    this.deg = new Int32Array(n);
    this.adj = Array.from({ length: n }, () => []);
    for (const [a, b] of map.edges) {
      this.deg[a]++;
      this.deg[b]++;
      this.adj[a].push(b);
      this.adj[b].push(a);
    }
    this.energy = new Float32Array(n);
    this.heat = new Float32Array(map.regions.length);
    const sums = map.regions.map(() => [0, 0, 0]);
    for (const nd of map.nodes) {
      sums[nd[3]][0] += nd[5];
      sums[nd[3]][1] += nd[6];
      sums[nd[3]][2]++;
    }
    this.centroids = sums.map(([x, y, c]) => (c ? [x / c, y / c] : [0, 0]));
    this.micNode = map.anchors.listen ?? -1;
    this.readColors();
  }

  readColors() {
    const css = getComputedStyle(document.documentElement);
    const v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
    this.colors = { text: v("--text", "#ededed"), text2: v("--text-2", "#a3a3a3"), text3: v("--text-3", "#6f6f6f"),
      accent: v("--accent", "#f5f5f5"), bg: v("--bg", "#141414"), font: v("--font", "sans-serif") };
    this.dirty = true;
  }

  resize(w: number, h: number) {
    const first = !this.w;
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.w = w;
    this.h = h;
    for (const c of [this.canvas, this.layer]) {
      c.width = Math.round(w * this.dpr);
      c.height = Math.round(h * this.dpr);
    }
    if (first) this.fit();
    this.dirty = true;
  }

  fit(region: number | null = null) {
    const nodes = this.map.nodes.filter((nd) => region === null || nd[3] === region);
    if (!nodes.length || !this.w) return;
    let [x0, y0, x1, y1] = [Infinity, Infinity, -Infinity, -Infinity];
    for (const nd of nodes) {
      x0 = Math.min(x0, nd[5]); x1 = Math.max(x1, nd[5]);
      y0 = Math.min(y0, nd[6]); y1 = Math.max(y1, nd[6]);
    }
    const top = Math.min(this.insetTop, this.h * 0.4);
    const h = this.h - top - 16;
    const s = Math.min(this.w / Math.max(x1 - x0, 1), h / Math.max(y1 - y0, 1)) * (region === null ? 0.92 : 0.6);
    this.scale = s;
    if (region === null) this.fitScale = s;
    this.ox = this.w / 2 - ((x0 + x1) / 2) * s;
    this.oy = top + h / 2 - ((y0 + y1) / 2) * s;
    this.dirty = true;
  }

  center(i: number) {
    const nd = this.map.nodes[i];
    this.scale = Math.max(this.scale, this.fitScale * 2.2);
    this.ox = this.w / 2 - nd[5] * this.scale;
    this.oy = this.h / 2 - nd[6] * this.scale;
    this.dirty = true;
  }

  zoomAt(sx: number, sy: number, factor: number) {
    const s = Math.min(Math.max(this.scale * factor, this.fitScale * 0.5), this.fitScale * 14);
    const f = s / this.scale;
    this.ox = sx - (sx - this.ox) * f;
    this.oy = sy - (sy - this.oy) * f;
    this.scale = s;
    this.dirty = true;
  }

  pan(dx: number, dy: number) {
    this.ox += dx;
    this.oy += dy;
    this.dirty = true;
  }

  sx(i: number) { return this.map.nodes[i][5] * this.scale + this.ox; }
  sy(i: number) { return this.map.nodes[i][6] * this.scale + this.oy; }

  radius(i: number) {
    const zoom = Math.min(2.4, Math.max(0.75, Math.sqrt(this.scale / this.fitScale)));
    return (1.1 + Math.sqrt(this.deg[i]) * 0.42) * zoom;
  }

  nodeAt(x: number, y: number): number {
    let best = -1;
    let bestD = Infinity;
    for (let i = 0; i < this.map.nodes.length; i++) {
      const d = Math.hypot(this.sx(i) - x, this.sy(i) - y);
      if (d < Math.max(7, this.radius(i) + 3) && d < bestD) {
        best = i;
        bestD = d;
      }
    }
    return best;
  }

  setEmphasis() {
    let set: Set<number> | null = null;
    if (this.selected >= 0) set = new Set([this.selected, ...this.adj[this.selected]]);
    else if (this.focusRegion !== null) {
      set = new Set();
      this.map.nodes.forEach((nd, i) => nd[3] === this.focusRegion && set!.add(i));
    }
    this.emphasis = set;
    this.dirty = true;
  }

  /** Light a node up and send signals along its connections, fading a little at every hop. */
  fire(i: number, strength = 1, hops = 2, now = performance.now()) {
    if (i < 0 || i >= this.energy.length) return;
    this.energy[i] = Math.max(this.energy[i], strength);
    const r = this.map.nodes[i][3];
    this.heat[r] = Math.max(this.heat[r], strength);
    if (this.reduced || hops <= 0) return;
    const nbs = [...this.adj[i]].sort(() => Math.random() - 0.5).slice(0, hops > 1 ? 9 : 3);
    for (const b of nbs) {
      const dist = Math.hypot(this.map.nodes[b][5] - this.map.nodes[i][5], this.map.nodes[b][6] - this.map.nodes[i][6]);
      this.pulses.push({ a: i, b, t0: now + Math.random() * 90, dur: Math.min(900, Math.max(260, dist * 1.1)), s: strength * 0.8, hops: hops - 1 });
    }
    if (this.pulses.length > 700) this.pulses.splice(0, this.pulses.length - 700);
  }

  drawLayer() {
    const { map, colors } = this;
    const g = this.layer.getContext("2d")!;
    g.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    g.clearRect(0, 0, this.w, this.h);
    const em = this.emphasis;
    g.lineWidth = 0.6;
    g.strokeStyle = colors.text2;
    g.globalAlpha = em ? 0.035 : 0.085;
    g.beginPath();
    for (const [a, b] of map.edges) {
      g.moveTo(this.sx(a), this.sy(a));
      g.lineTo(this.sx(b), this.sy(b));
    }
    g.stroke();
    if (em) {
      g.globalAlpha = 0.32;
      g.strokeStyle = colors.text;
      g.beginPath();
      for (const [a, b] of map.edges) {
        const hit = this.selected >= 0 ? a === this.selected || b === this.selected : em.has(a) && em.has(b);
        if (!hit) continue;
        g.moveTo(this.sx(a), this.sy(a));
        g.lineTo(this.sx(b), this.sy(b));
      }
      g.stroke();
    }
    for (let i = 0; i < map.nodes.length; i++) {
      const x = this.sx(i);
      const y = this.sy(i);
      if (x < -20 || y < -20 || x > this.w + 20 || y > this.h + 20) continue;
      const on = !em || em.has(i);
      g.globalAlpha = on ? (map.nodes[i][4] === "F" ? 0.95 : 0.7) : 0.16;
      g.fillStyle = on && em ? colors.text : colors.text2;
      g.beginPath();
      g.arc(x, y, this.radius(i), 0, Math.PI * 2);
      g.fill();
    }
    g.globalAlpha = 1;
    this.dirty = false;
  }

  frame = (now: number) => {
    const dt = Math.min(now - (this.last || now), 100);
    this.last = now;
    if (this.dirty) this.drawLayer();
    const { ctx, colors, map } = this;
    const decay = Math.exp(-dt / 650);
    for (let i = 0; i < this.energy.length; i++) if (this.energy[i] > 0.004) this.energy[i] *= decay; else this.energy[i] = 0;
    for (let r = 0; r < this.heat.length; r++) this.heat[r] *= Math.exp(-dt / 1400);

    if (!this.reduced && now > this.nextSpark) {  // a resting brain still flickers
      const i = Math.floor(Math.random() * map.nodes.length);
      this.fire(i, 0.28, Math.random() < 0.18 ? 1 : 0, now);
      this.nextSpark = now + 120 + Math.random() * 220;
    }
    if (this.micNode >= 0 && this.listening()) this.energy[this.micNode] = Math.max(this.energy[this.micNode], Math.min(1, levels.mic * 3));

    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.w, this.h);
    ctx.drawImage(this.layer, 0, 0, this.w, this.h);

    // Signals travelling along connections.
    ctx.strokeStyle = colors.accent;
    ctx.fillStyle = colors.accent;
    ctx.lineCap = "round";
    const live: Pulse[] = [];
    for (const p of this.pulses) {
      const t = (now - p.t0) / p.dur;
      if (t < 0) { live.push(p); continue; }
      if (t >= 1) {
        this.energy[p.b] = Math.max(this.energy[p.b], p.s * 0.75);
        if (p.hops > 0 && p.s > 0.3) this.fire(p.b, p.s * 0.7, p.hops, now);
        continue;
      }
      live.push(p);
      const ax = this.sx(p.a), ay = this.sy(p.a), bx = this.sx(p.b), by = this.sy(p.b);
      const tail = Math.max(0, t - 0.28);
      ctx.globalAlpha = p.s * 0.55;
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(ax + (bx - ax) * tail, ay + (by - ay) * tail);
      ctx.lineTo(ax + (bx - ax) * t, ay + (by - ay) * t);
      ctx.stroke();
      ctx.globalAlpha = p.s;
      ctx.beginPath();
      ctx.arc(ax + (bx - ax) * t, ay + (by - ay) * t, 1.3 + p.s, 0, Math.PI * 2);
      ctx.fill();
    }
    this.pulses = live;

    // Glow on whatever is active.
    for (let i = 0; i < this.energy.length; i++) {
      const e = this.energy[i];
      if (e < 0.03) continue;
      const x = this.sx(i), y = this.sy(i), r = this.radius(i);
      ctx.globalAlpha = e * 0.16;
      ctx.beginPath();
      ctx.arc(x, y, r * 2.4 + e * 9, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = Math.min(1, 0.35 + e);
      ctx.beginPath();
      ctx.arc(x, y, r * (1 + e * 0.6), 0, Math.PI * 2);
      ctx.fill();
    }

    // Region names, brighter while that region is busy.
    ctx.font = `600 12px ${colors.font}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    map.regions.forEach((reg, r) => {
      if (!reg.nodes) return;
      const [wx, wy] = this.centroids[r];
      const x = wx * this.scale + this.ox, y = wy * this.scale + this.oy;
      const dim = this.focusRegion !== null && this.focusRegion !== r;
      ctx.globalAlpha = dim ? 0.25 : 0.55 + Math.min(0.45, this.heat[r] * 0.6);
      ctx.lineWidth = 4;
      ctx.lineJoin = "round";
      ctx.strokeStyle = colors.bg;
      ctx.strokeText(reg.name.toUpperCase(), x, y);
      ctx.fillStyle = this.heat[r] > 0.35 && !dim ? colors.text : colors.text2;
      ctx.fillText(reg.name.toUpperCase(), x, y);
    });

    for (const [i, ring] of [[this.hover, 4], [this.selected, 6]] as const) {
      if (i < 0) continue;
      ctx.globalAlpha = 0.9;
      ctx.strokeStyle = colors.text;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(this.sx(i), this.sy(i), this.radius(i) + ring, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
    this.raf = requestAnimationFrame(this.frame);
  };

  start() { this.raf = requestAnimationFrame(this.frame); }
  stop() { cancelAnimationFrame(this.raf); }
}

/** What lights up, and what the live feed says, for one engine event. */
function signalsFor(ev: { type: string; [k: string]: any }, map: BrainMap, seen: Set<string>, backup: boolean):
  { node: number; text?: string; s?: number; hops?: number }[] {
  const a = map.anchors;
  const at = (key: string, text?: string, s = 1, hops = 2) => (a[key] === undefined ? [] : [{ node: a[key], text, s, hops }]);
  switch (ev.type) {
    case "message.start":
      return [...at("think", "Thinking"), ...at(backup ? "backup" : "gemini", undefined, 0.8)];
    case "message.delta":
      return at(backup ? "backup" : "gemini", undefined, 0.45, 1);
    case "message.end":
      return at("interface", "Answered", 0.7);
    case "task": {
      const out: { node: number; text?: string; s?: number; hops?: number }[] = [];
      for (const step of ev.task?.steps ?? []) {
        const key = `${step.id}:${step.status}`;
        if (seen.has(key)) continue;
        seen.add(key);
        if (step.status === "running") {
          const src = map.tool_sources[step.tool];
          const node = map.tools[step.tool] ?? a[src === "mcp" ? "connector" : src === "plugin" ? "plugin" : "task"];
          if (node !== undefined) out.push({ node, text: `Using ${step.tool.split("__").pop()}`, s: 1, hops: 3 });
          out.push(...at("task", undefined, 0.6, 1));
        } else if (step.status === "waiting") {
          out.push(...at("approval", "Waiting for your OK"));
        }
      }
      if (seen.size > 2000) seen.clear();
      return out;
    }
    case "approval.request":
      return at("approval", "Asking for approval");
    case "voice.state":
      if (ev.state === "listening") return [...at("listen", "Listening"), ...at("voice", undefined, 0.6)];
      if (ev.state === "thinking") return at("think", "Thinking about what you said");
      if (ev.state === "speaking") return at("speak", "Speaking");
      return [];
    case "voice.heard":
      return ev.accepted ? at("wake", `Heard “${ev.phrase}”`) : [];
    case "voice.transcript":
      return ev.role === "user" && !ev.tentative ? at("listen", "Heard you", 0.8) : [];
    case "memory.changed":
      return at("memory", "Updating memory");
    case "alert":
      return at("alert", ev.alert?.title ? `Noticed: ${ev.alert.title}` : "Noticed something");
    case "missions.changed":
      return at("mission", "Working on a mission");
    case "routines.changed":
      return at("routine", "Routines changed");
    case "brain.backup":
      return ev.active ? at("backup", "Switched to the backup brain") : at("gemini", "Back on Gemini");
    case "ecosystem.changed":
      return at("connector", "Connections changed");
    case "settings.changed":
      return at("engine", "Settings changed", 0.8);
    case "notify":
      return at("interface", ev.title || "Notification", 0.8);
    default:
      return [];
  }
}

export function BrainView() {
  const [map, setMap] = useState<BrainMap | null>(null);
  const [error, setError] = useState<string | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const brain = useRef<BrainCanvas | null>(null);
  const head = useRef<HTMLDivElement>(null);
  const [feed, setFeed] = useState<Signal[]>([]);
  const [selected, setSelected] = useState(-1);
  const [hover, setHover] = useState<{ i: number; x: number; y: number } | null>(null);
  const [region, setRegion] = useState<number | null>(null);
  const [q, setQ] = useState("");
  const theme = useStore((s) => s.engine?.settings.theme);

  useEffect(() => {
    api.get<BrainMap | { available: false }>("/api/brain")
      .then((m) => (m.available ? setMap(m) : setError("This copy of ORION was built without its brain map.")))
      .catch((e) => setError((e as Error).message));
  }, []);

  useEffect(() => {
    if (!map || !canvas.current || !wrap.current) return;
    const b = new BrainCanvas(canvas.current, map);
    b.listening = () => useStore.getState().voice.state === "listening";
    brain.current = b;
    const ro = new ResizeObserver(([entry]) => {
      const el = head.current;
      b.insetTop = el ? el.offsetTop + el.offsetHeight + 12 : 0;
      b.resize(entry.contentRect.width, entry.contentRect.height);
    });
    ro.observe(wrap.current);
    b.start();

    let seq = 1;
    const seen = new Set<string>();
    let lastDelta = 0;
    const said = new Map<string, number>();  // some events arrive twice; list each happening once
    const tap = (ev: { type: string; [k: string]: any }) => {
      if (ev.type === "message.delta") {
        if (performance.now() - lastDelta < 350) return;
        lastDelta = performance.now();
      }
      const backup = !!useStore.getState().engine?.backup?.active;
      const sigs = signalsFor(ev, map, seen, backup);
      for (const sg of sigs) b.fire(sg.node, sg.s ?? 1, sg.hops ?? 2);
      const now = Date.now();
      const named = sigs.filter((sg) => sg.text && now - (said.get(sg.text) ?? 0) > 1500);
      for (const sg of named) said.set(sg.text!, now);
      if (named.length) {
        setFeed((f) => [...named.map((sg) => ({ id: seq++, at: now, text: sg.text!, node: sg.node })), ...f].slice(0, FEED_MAX));
      }
    };
    eventTaps.add(tap);
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const recolor = () => requestAnimationFrame(() => b.readColors());
    mq.addEventListener("change", recolor);
    return () => {
      eventTaps.delete(tap);
      mq.removeEventListener("change", recolor);
      ro.disconnect();
      b.stop();
      brain.current = null;
    };
  }, [map]);

  useEffect(() => { requestAnimationFrame(() => brain.current?.readColors()); }, [theme]);

  // Pointer: drag to pan, wheel to zoom, click to select.
  useEffect(() => {
    const el = canvas.current;
    const b = brain.current;
    if (!el || !b) return;
    let drag: { x: number; y: number; moved: boolean } | null = null;
    const local = (e: MouseEvent) => {
      const r = el.getBoundingClientRect();
      return [e.clientX - r.left, e.clientY - r.top] as const;
    };
    const down = (e: PointerEvent) => {
      drag = { x: e.clientX, y: e.clientY, moved: false };
      el.setPointerCapture(e.pointerId);
    };
    const move = (e: PointerEvent) => {
      if (drag) {
        const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        if (Math.abs(dx) + Math.abs(dy) > 2) drag.moved = true;
        if (drag.moved) {
          b.pan(dx, dy);
          drag.x = e.clientX;
          drag.y = e.clientY;
          el.style.cursor = "grabbing";
          return;
        }
      }
      const [x, y] = local(e);
      const i = b.nodeAt(x, y);
      b.hover = i;
      el.style.cursor = i >= 0 ? "pointer" : "grab";
      setHover(i >= 0 ? { i, x, y } : null);
    };
    const up = (e: PointerEvent) => {
      const click = drag && !drag.moved;
      drag = null;
      el.style.cursor = "grab";
      if (!click) return;
      const [x, y] = local(e);
      const i = b.nodeAt(x, y);
      b.selected = i;
      b.setEmphasis();
      setSelected(i);
      if (i >= 0) b.fire(i, 1, 2);
    };
    const leave = () => { b.hover = -1; setHover(null); };
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      const [x, y] = local(e);
      b.zoomAt(x, y, Math.exp(-e.deltaY * 0.0015));
      setHover(null);
    };
    el.addEventListener("pointerdown", down);
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
    el.addEventListener("pointerleave", leave);
    el.addEventListener("wheel", wheel, { passive: false });
    return () => {
      el.removeEventListener("pointerdown", down);
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
      el.removeEventListener("pointerleave", leave);
      el.removeEventListener("wheel", wheel);
    };
  }, [map]);

  const matches = useMemo(() => {
    const f = q.trim().toLowerCase();
    if (!map || f.length < 2) return [];
    return map.nodes.map((nd, i) => [nd, i] as const).filter(([nd]) => nd[0].toLowerCase().includes(f)).map(([, i]) => i);
  }, [map, q]);

  const select = (i: number) => {
    const b = brain.current;
    if (!b) return;
    b.selected = i;
    b.setEmphasis();
    setSelected(i);
    if (i >= 0) {
      b.center(i);
      b.fire(i, 1, 2);
    }
  };

  const focusRegion = (r: number | null) => {
    const b = brain.current;
    if (!b) return;
    b.focusRegion = r;
    b.selected = -1;
    setSelected(-1);
    b.setEmphasis();
    b.fit(r);
    setRegion(r);
  };

  const spark = () => {
    const b = brain.current;
    if (!b || !map) return;
    const start = map.anchors.engine ?? 0;
    b.fire(start, 1, 4);
    setFeed((f) => [{ id: Date.now(), at: Date.now(), text: "Test signal from the engine", node: start }, ...f].slice(0, FEED_MAX));
  };

  if (error) {
    return (
      <div className="page"><div className="page-inner">
        <div className="empty"><BrainCircuit size={28} /><div>The brain map couldn't load.</div><div className="muted">{error}</div></div>
      </div></div>
    );
  }

  const nd = map && selected >= 0 ? map.nodes[selected] : null;
  const neighbours = map && selected >= 0 && brain.current
    ? [...brain.current.adj[selected]].sort((x, y) => brain.current!.deg[y] - brain.current!.deg[x]) : [];
  const hv = map && hover ? map.nodes[hover.i] : null;

  return (
    <div className="brain" ref={wrap}>
      <canvas ref={canvas} role="img"
        aria-label={map ? `Map of ORION's code: ${map.nodes.length} parts in ${map.regions.length} regions. Parts light up as ORION uses them.` : "Loading"} />

      <div className="brain-head" ref={head}>
        <h2>ORION's brain</h2>
        <p>
          {map ? <>Every dot is a part of ORION's code, {map.nodes.length.toLocaleString()} in all, with {map.edges.length.toLocaleString()} connections between them. </> : "Loading the map… "}
          Parts light up live as ORION thinks, listens, speaks and uses its tools.
        </p>
        {map && (
          <div className="brain-regions" role="group" aria-label="Brain regions">
            {map.regions.filter((r) => r.nodes).map((r) => {
              const i = map.regions.indexOf(r);
              return (
                <button key={r.key} className={`brain-chip ${region === i ? "on" : ""}`} title={r.about}
                  onClick={() => focusRegion(region === i ? null : i)}>
                  {r.name} <span>{r.nodes}</span>
                </button>
              );
            })}
          </div>
        )}
      </div>

      <div className="brain-search">
        <Search size={14} />
        <input value={q} placeholder="Find a part…" aria-label="Find a part of ORION's brain"
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && matches.length) { select(matches[0]); setQ(""); }
            if (e.key === "Escape") setQ("");
          }} />
        {q.trim().length >= 2 && <span className="brain-count">{matches.length}</span>}
        {q.trim().length >= 2 && matches.length > 0 && (
          <div className="brain-results">
            {matches.slice(0, 8).map((i) => (
              <button key={i} onClick={() => { select(i); setQ(""); }}>
                <b>{map!.nodes[i][0]}</b><span>{map!.regions[map!.nodes[i][3]].name}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {hv && hover && hover.i !== selected && (
        <div className="brain-tip" style={{ left: hover.x + 14, top: hover.y + 14 }}>
          <b>{hv[0]}</b>
          <span>{KIND[hv[4]]} · {map!.regions[hv[3]].name}</span>
        </div>
      )}

      <div className="brain-feed" aria-live="polite">
        <div className="brain-feed-head"><span className={`dot ${feed.length ? "ok pulse" : ""}`} /> Live</div>
        {feed.length === 0 && <div className="brain-feed-empty">Waiting for ORION to do something. Ask it anything and watch.</div>}
        {feed.map((sg) => (
          <button key={sg.id} className="brain-feed-row" onClick={() => select(sg.node)} title="Show this part">
            <span className="t">{sg.text}</span>
            <span className="r">{map?.regions[map.nodes[sg.node][3]].name}</span>
          </button>
        ))}
      </div>

      {nd && map && (
        <div className="brain-card">
          <div className="brain-card-head">
            <div className="grow">
              <div className="brain-card-name">{nd[0]}</div>
              <div className="brain-card-sub">{KIND[nd[4]]} in {map.regions[nd[3]].name}</div>
            </div>
            <button className="icon-btn sm" aria-label="Close" onClick={() => select(-1)}><X size={14} /></button>
          </div>
          <div className="brain-card-file">{map.files[nd[1]]}{nd[2] > 1 ? `:${nd[2]}` : ""}</div>
          <div className="brain-card-about">{map.regions[nd[3]].about}</div>
          <div className="brain-card-label">{neighbours.length} connection{neighbours.length === 1 ? "" : "s"}</div>
          <div className="brain-card-list">
            {neighbours.slice(0, 12).map((i) => (
              <button key={i} onClick={() => select(i)}>
                <span>{map.nodes[i][0]}</span><span className="r">{map.regions[map.nodes[i][3]].name}</span>
              </button>
            ))}
            {neighbours.length > 12 && <div className="more">and {neighbours.length - 12} more</div>}
          </div>
        </div>
      )}

      <div className="brain-tools">
        <button className="icon-btn" title="Send a test signal" aria-label="Send a test signal" onClick={spark}><Zap size={15} /></button>
        <button className="icon-btn" title="Zoom in" aria-label="Zoom in" onClick={() => brain.current?.zoomAt(brain.current.w / 2, brain.current.h / 2, 1.4)}><Plus size={15} /></button>
        <button className="icon-btn" title="Zoom out" aria-label="Zoom out" onClick={() => brain.current?.zoomAt(brain.current.w / 2, brain.current.h / 2, 1 / 1.4)}><Minus size={15} /></button>
        <button className="icon-btn" title="Show the whole brain" aria-label="Show the whole brain" onClick={() => focusRegion(null)}><Crosshair size={15} /></button>
      </div>

      {map && <div className="brain-foot">Mapped from ORION {map.orion_version}'s source with graphify</div>}
    </div>
  );
}
