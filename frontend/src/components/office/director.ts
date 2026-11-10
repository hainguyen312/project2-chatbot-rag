"use client";

/**
 * "Đạo diễn" văn phòng:
 *  - nhận SSE event từ /rag/stream (hoặc mock) → kịch bản hành động (Step) cho từng nhân vật
 *  - sinh hành vi lúc rảnh (ngồi làm việc, lấy nước, tán gẫu) cho văn phòng luôn "sống"
 *  - quản lý chế độ camera (toàn cảnh / nhập vai / mắt nhân vật) và hội thoại với người chơi
 * Mỗi Character tự chạy hàng đợi Step của mình trong useFrame và gọi `advance()` khi xong.
 */
import { useSyncExternalStore } from "react";
import {
  CHARACTER_BY_ID,
  CHARACTERS,
  SMALL_TALK,
  STAFF,
  STATIONS,
  TOOL_ROUTES,
  type CharacterId,
  type Vec2,
} from "./officeData";
import { characterPositions } from "./runtime";

export type Anim = "idle" | "think" | "search" | "type" | "read" | "talk" | "drink" | "wave";

/** Đích đi tới: toạ độ cố định, hoặc "đến gần nhân vật X" (bám theo nếu X di chuyển) */
export type Target = Vec2 | { near: CharacterId; dist?: number };

type Common = { idle?: boolean };
export type Step = Common &
  (
    | { kind: "walk"; to: Target; carry?: boolean; run?: boolean }
    | { kind: "face"; to: Target | number }
    | { kind: "work"; anim: Anim; minMs: number; until?: string; activity: string; seated?: boolean; facing?: number }
    | { kind: "say"; text: string | (() => string); ms: number }
    | { kind: "drop"; callId?: string }
    | { kind: "signal"; token: string }
  );

export type Passage = {
  mapc: string;
  ten: string;
  tenchuong?: string;
  tendemuc?: string;
  tenchude?: string;
  noidung: string;
  score?: number;
  source?: string;
  url?: string;
};

export type StreamEvent = {
  type: "status" | "meta" | "token" | "done" | "error";
  stage?: string;
  text?: string;
  tool?: string;
  call_id?: string;
  args?: Record<string, unknown>;
  count?: number;
  titles?: string[];
  action?: string;
  normalized_query?: string;
  in_scope?: boolean;
  hit?: boolean;
  passages?: Passage[];
  iteration?: number;
  max?: number;
  message?: string;
  cache_hit?: boolean;
  confidence_score?: number;
};

export type CharView = {
  id: CharacterId;
  bubble: string | null;
  activity: string;
  carrying: boolean;
  busy: boolean; // đang làm việc thật (không tính hoạt động lúc rảnh)
  log: { t: number; text: string }[];
};

type CharRuntime = {
  id: CharacterId;
  queue: Step[];
  bubble: string | null;
  bubbleUntil: number;
  activity: string;
  carrying: boolean;
  log: { t: number; text: string }[];
};

export type ViewMode = "orbit" | "fp" | "pov";
export type TimelineItem = { t: number; who: CharacterId; text: string };

type Snapshot = {
  chars: Record<CharacterId, CharView>;
  deskDocs: number;
  passages: Passage[];
  timeline: TimelineItem[];
  selected: CharacterId | null;
  iteration: { cur: number; max: number } | null;
  view: ViewMode;
  povId: CharacterId;
  focus: CharacterId | null; // NPC người chơi đang nhìn vào (nhập vai)
  dialog: CharacterId | null;
};

const short = (s: string, n = 60) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
const rand = (a: number, b: number) => a + Math.random() * (b - a);
const pick = <T,>(arr: readonly T[]) => arr[Math.floor(Math.random() * arr.length)];

class Director {
  private chars = new Map<CharacterId, CharRuntime>();
  private resolved = new Set<string>();
  private pending = new Set<string>();
  private results = new Map<string, { count: number; titles: string[] }>();
  private listeners = new Set<() => void>();
  private views = new Map<CharacterId, CharView>();
  private snap: Snapshot;
  private deskDocs = 0;
  private passages: Passage[] = [];
  private timeline: TimelineItem[] = [];
  private selected: CharacterId | null = null;
  private iteration: Snapshot["iteration"] = null;
  private view: ViewMode = "orbit";
  private povId: CharacterId = "librarian";
  private focus: CharacterId | null = null;
  private dialog: CharacterId | null = null;
  private dirty = false;

  constructor() {
    for (const c of CHARACTERS) {
      this.chars.set(c.id, {
        id: c.id,
        queue: [],
        bubble: null,
        bubbleUntil: 0,
        activity: "Đang rảnh",
        carrying: false,
        log: [],
      });
    }
    this.snap = this.buildSnap();
  }

  // ── store plumbing ─────────────────────────────────────────────────────────
  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };
  getSnapshot = () => this.snap;

  private viewOf(c: CharRuntime): CharView {
    const busy = c.queue.some((s) => !s.idle);
    const prev = this.views.get(c.id);
    if (
      prev &&
      prev.bubble === c.bubble &&
      prev.activity === c.activity &&
      prev.carrying === c.carrying &&
      prev.busy === busy &&
      prev.log === c.log
    )
      return prev; // giữ nguyên tham chiếu → component không re-render
    const v: CharView = { id: c.id, bubble: c.bubble, activity: c.activity, carrying: c.carrying, busy, log: c.log };
    this.views.set(c.id, v);
    return v;
  }

  private buildSnap(): Snapshot {
    const chars = {} as Record<CharacterId, CharView>;
    for (const [id, c] of this.chars) chars[id] = this.viewOf(c);
    return {
      chars,
      deskDocs: this.deskDocs,
      passages: this.passages,
      timeline: this.timeline,
      selected: this.selected,
      iteration: this.iteration,
      view: this.view,
      povId: this.povId,
      focus: this.focus,
      dialog: this.dialog,
    };
  }
  /** Gom các thay đổi trong cùng frame thành 1 lần emit */
  private emit() {
    if (this.dirty) return;
    this.dirty = true;
    queueMicrotask(() => {
      this.dirty = false;
      this.snap = this.buildSnap();
      this.listeners.forEach((l) => l());
    });
  }

  // ── API cho Character (gọi trong useFrame) ────────────────────────────────
  current(id: CharacterId): Step | undefined {
    return this.chars.get(id)?.queue[0];
  }
  isResolved(token?: string) {
    return !token || this.resolved.has(token);
  }
  advance(id: CharacterId) {
    const c = this.chars.get(id);
    if (!c) return;
    c.queue.shift();
    this.enterStep(c);
    this.emit();
  }
  tick(now: number) {
    for (const c of this.chars.values()) {
      if (c.bubble && c.bubbleUntil && now > c.bubbleUntil) {
        c.bubble = null;
        c.bubbleUntil = 0;
        this.emit();
      }
    }
  }

  private enterStep(c: CharRuntime) {
    const step = c.queue[0];
    if (!step) {
      c.activity = "Đang rảnh";
      c.carrying = false;
      return;
    }
    switch (step.kind) {
      case "walk":
        c.carrying = !!step.carry;
        if (!step.idle) c.activity = step.carry ? "Mang tài liệu về" : "Đang di chuyển";
        break;
      case "work":
        c.activity = step.activity;
        break;
      case "say": {
        const text = typeof step.text === "function" ? step.text() : step.text;
        this.say(c.id, text, step.ms, !step.idle);
        break;
      }
      case "drop": {
        const r = step.callId ? this.results.get(step.callId) : undefined;
        this.deskDocs += Math.max(1, Math.min(r?.count ?? 1, 6));
        c.carrying = false;
        c.queue.shift();
        this.enterStep(c);
        break;
      }
      case "signal":
        this.resolved.add(step.token);
        c.queue.shift();
        this.enterStep(c);
        break;
      case "face":
        break;
    }
  }

  private push(id: CharacterId, ...steps: Step[]) {
    const c = this.chars.get(id);
    if (!c) return;
    // việc thật luôn được ưu tiên hơn hoạt động lúc rảnh
    if (!steps.every((s) => s.idle) && c.queue.length && c.queue.every((s) => s.idle)) c.queue = [];
    const wasIdle = c.queue.length === 0;
    c.queue.push(...steps);
    if (wasIdle) this.enterStep(c);
    this.emit();
  }

  private say(id: CharacterId, text: string, ms = 3000, log = true) {
    const c = this.chars.get(id);
    if (!c) return;
    c.bubble = text;
    c.bubbleUntil = performance.now() + ms;
    if (log) this.log(id, text);
    this.emit();
  }

  private log(id: CharacterId, text: string) {
    const c = this.chars.get(id);
    if (!c) return;
    const item = { t: Date.now(), text };
    c.log = [...c.log.slice(-29), item];
    this.timeline = [...this.timeline.slice(-79), { ...item, who: id }];
  }

  private expect(token: string) {
    this.pending.add(token);
    return token;
  }
  private resolve(token: string) {
    this.resolved.add(token);
    this.pending.delete(token);
  }

  // ── Chọn nhân vật / camera / hội thoại ────────────────────────────────────
  select(id: CharacterId | null) {
    this.selected = id;
    this.emit();
  }
  setView(mode: ViewMode, povId?: CharacterId) {
    const prev = this.view;
    this.view = mode;
    if (povId) this.povId = povId;
    if (mode !== "fp") this.focus = null;
    if (prev === "fp" && mode !== "fp") {
      // khách trở về ghế
      this.chars.get("client")!.queue = [];
      this.push("client", { kind: "walk", to: STATIONS.clientChair, idle: true });
    }
    this.emit();
  }
  setFocus(id: CharacterId | null) {
    if (id === this.focus) return;
    this.focus = id;
    this.emit();
  }
  isIdle(id: CharacterId) {
    const c = this.chars.get(id);
    return !c || c.queue.every((s) => s.idle);
  }

  openDialog(id: CharacterId) {
    if (id === "client") return;
    this.dialog = id;
    const def = CHARACTER_BY_ID[id];
    const token = `dialog:${id}:${Date.now()}`;
    this.dialogToken = token;
    if (this.isIdle(id)) {
      this.chars.get(id)!.queue = [];
      this.push(
        id,
        { kind: "face", to: { near: "client" }, idle: true },
        { kind: "say", text: def.greet, ms: 4000, idle: true },
        { kind: "work", anim: "talk", minMs: 800, until: token, activity: "Đang nói chuyện với bạn", idle: true, facing: undefined },
      );
    } else {
      this.say(id, "Tôi đang bận việc của văn phòng, nói nhanh nhé!", 3000, false);
    }
    this.emit();
  }
  private dialogToken = "";
  closeDialog() {
    if (this.dialogToken) this.resolved.add(this.dialogToken);
    if (this.dialog) this.say(this.dialog, "Hẹn gặp lại ạ!", 1800, false);
    this.dialog = null;
    this.emit();
  }
  /** NPC phản hồi trong hộp hội thoại */
  npcSay(id: CharacterId, text: string, ms = 4000) {
    this.say(id, text, ms, false);
  }

  // ── Hoạt động lúc rảnh ────────────────────────────────────────────────────
  /** Character gọi khi đã rảnh một lúc → giao một hoạt động ngẫu nhiên */
  idle(id: CharacterId) {
    if (id === "client") {
      if (this.view === "fp") return;
      const def = CHARACTER_BY_ID.client;
      this.push(id, {
        kind: "work", anim: "think", minMs: rand(6000, 12000), activity: "Chờ tư vấn",
        seated: true, facing: def.work.facing, idle: true,
      });
      return;
    }
    if (this.dialog === id) return;
    const def = CHARACTER_BY_ID[id];
    const sitAtWork = (ms: number): Step[] => [
      { kind: "walk", to: def.work.pos, idle: true },
      {
        kind: "work", anim: def.work.anim, minMs: ms, activity: "Làm việc tại chỗ",
        seated: def.work.seated, facing: def.work.facing, idle: true,
      },
    ];
    const r = Math.random();
    // luật sư & lễ tân chủ yếu ngồi tại chỗ
    const homebody = id === "lead" || id === "receptionist";
    if (r < (homebody ? 0.8 : 0.5)) {
      this.push(id, ...sitAtWork(rand(7000, 15000)));
    } else if (r < (homebody ? 0.9 : 0.72)) {
      this.push(
        id,
        { kind: "walk", to: STATIONS.cooler, idle: true },
        { kind: "work", anim: "drink", minMs: rand(2500, 4000), activity: "Lấy nước", facing: Math.PI, idle: true },
        ...sitAtWork(rand(6000, 10000)),
      );
    } else {
      // sang tán gẫu với một đồng nghiệp đang rảnh
      const mates = STAFF.filter((m) => m.id !== id && this.isIdle(m.id) && this.dialog !== m.id);
      if (!mates.length) {
        this.push(id, ...sitAtWork(rand(6000, 10000)));
        return;
      }
      const mate = pick(mates).id;
      const [a, b] = pick(SMALL_TALK);
      const talkMs = 5200;
      this.chars.get(mate)!.queue = [];
      this.push(
        mate,
        { kind: "face", to: { near: id }, idle: true },
        { kind: "work", anim: "idle", minMs: 1600, activity: "Tán gẫu", idle: true },
        { kind: "say", text: b, ms: 2800, idle: true },
        { kind: "work", anim: "talk", minMs: 1600, activity: "Tán gẫu", idle: true },
      );
      this.push(
        id,
        { kind: "walk", to: { near: mate, dist: 1.1 }, idle: true },
        { kind: "face", to: { near: mate }, idle: true },
        { kind: "say", text: a, ms: 2800, idle: true },
        { kind: "work", anim: "talk", minMs: talkMs - 1700, activity: "Tán gẫu", idle: true },
        ...sitAtWork(rand(6000, 10000)),
      );
    }
  }

  // ── Kịch bản cho một câu hỏi ──────────────────────────────────────────────
  beginQuestion(prompt: string) {
    this.resolved.clear();
    this.pending.clear();
    this.results.clear();
    this.deskDocs = 0;
    this.passages = [];
    this.iteration = null;
    this.say("client", short(prompt, 90), 4500);
    this.emit();
  }

  handle(evt: StreamEvent) {
    switch (evt.type) {
      case "status":
        this.onStatus(evt);
        break;
      case "meta":
        this.passages = evt.passages ?? [];
        this.deskDocs = Math.max(this.deskDocs, Math.min(this.passages.length, 12));
        if (evt.cache_hit) this.log("lead", "Dùng lại câu trả lời trong sổ tay (cache)");
        break;
      case "done":
      case "error":
        this.resolve("answer");
        for (const t of [...this.pending]) this.resolve(t);
        if (evt.type === "error") this.say("lead", "Xin lỗi, văn phòng gặp sự cố: " + short(evt.message ?? "", 60), 5000);
        break;
    }
    this.emit();
  }

  private onStatus(evt: StreamEvent) {
    if (evt.iteration != null && evt.max != null) this.iteration = { cur: evt.iteration, max: evt.max };
    const L = STATIONS;
    const toClient: Target = { near: "client", dist: 1.15 };
    switch (evt.stage) {
      case "reception":
        this.push("receptionist", {
          kind: "work", anim: "think", minMs: 900, until: this.expect("reception"), activity: "Phân loại yêu cầu",
        });
        break;

      case "reception_done": {
        this.resolve("reception");
        const work = CHARACTER_BY_ID.receptionist.work;
        if (evt.action === "quick") {
          this.push(
            "receptionist",
            { kind: "walk", to: toClient },
            { kind: "face", to: { near: "client" } },
            { kind: "say", text: "Câu này tôi trả lời ngay được ạ!", ms: 2400 },
            { kind: "work", anim: "talk", minMs: 2200, until: this.expect("answer"), activity: "Trả lời khách" },
            { kind: "walk", to: work.pos },
          );
        } else if (evt.action === "spam") {
          this.push("receptionist", { kind: "say", text: "Yêu cầu này có dấu hiệu spam, tôi xin phép từ chối.", ms: 3500 });
        } else if (evt.action === "escalate") {
          this.push(
            "receptionist",
            { kind: "walk", to: toClient },
            { kind: "face", to: { near: "client" } },
            { kind: "say", text: "Tôi rất tiếc… Tôi sẽ chuyển cán bộ hỗ trợ trực tiếp cho anh/chị.", ms: 4000 },
            { kind: "work", anim: "talk", minMs: 2500, activity: "Trấn an khách" },
            { kind: "walk", to: work.pos },
          );
        } else {
          this.push("receptionist", { kind: "say", text: "Mời anh/chị trao đổi với luật sư ạ.", ms: 2200 });
        }
        break;
      }

      case "secretary":
        this.push(
          "secretary",
          { kind: "walk", to: toClient, run: true },
          { kind: "face", to: { near: "client" } },
          { kind: "work", anim: "type", minMs: 1400, until: this.expect("secretary"), activity: "Ghi chép & chuẩn hoá câu hỏi" },
        );
        break;

      case "secretary_done": {
        this.resolve("secretary");
        const q = evt.normalized_query ?? "";
        const work = CHARACTER_BY_ID.secretary.work;
        if (evt.in_scope === false) {
          this.push(
            "secretary",
            { kind: "say", text: "Yêu cầu này nằm ngoài phạm vi tư vấn pháp luật ạ.", ms: 3500 },
            { kind: "walk", to: work.pos },
          );
        } else {
          this.push(
            "secretary",
            { kind: "walk", to: L.secretaryReport, carry: true },
            { kind: "face", to: { near: "lead" } },
            { kind: "say", text: `Thưa LS: "${short(q, 70)}"`, ms: 3200 },
            { kind: "work", anim: "talk", minMs: 1200, activity: "Báo cáo luật sư" },
            { kind: "walk", to: work.pos },
          );
          this.push(
            "lead",
            { kind: "walk", to: CHARACTER_BY_ID.lead.work.pos },
            { kind: "work", anim: "think", minMs: 1500, activity: "Nghiên cứu vụ việc", seated: true, facing: 0 },
          );
        }
        break;
      }

      case "memory": {
        const n = evt.count ?? 0;
        this.push(
          "memory",
          { kind: "walk", to: L.memory },
          { kind: "work", anim: "search", minMs: 1300, activity: "Tra hồ sơ khách hàng", facing: -Math.PI / 2 },
          { kind: "walk", to: L.memoryReport, carry: n > 0 },
          { kind: "face", to: { near: "lead" } },
          { kind: "say", text: n > 0 ? `Hồ sơ có ${n} ghi chú về khách.` : "Khách mới, chưa có hồ sơ.", ms: 2600 },
          { kind: "work", anim: "talk", minMs: 900, activity: "Báo cáo hồ sơ" },
          { kind: "walk", to: CHARACTER_BY_ID.memory.work.pos },
        );
        break;
      }

      case "cache":
        if (evt.hit) {
          this.push(
            "lead",
            { kind: "walk", to: L.board },
            { kind: "work", anim: "read", minMs: 1500, activity: "Đọc sổ tay án lệ", facing: Math.PI },
            { kind: "say", text: "Câu này văn phòng đã trả lời rồi!", ms: 2500 },
            { kind: "walk", to: toClient },
            { kind: "face", to: { near: "client" } },
            { kind: "work", anim: "talk", minMs: 1800, until: this.expect("answer"), activity: "Trả lời khách" },
            { kind: "walk", to: CHARACTER_BY_ID.lead.work.pos },
          );
        }
        break;

      case "tool": {
        const route = evt.tool ? TOOL_ROUTES[evt.tool] : undefined;
        if (!route || !evt.call_id) break;
        const token = this.expect(`tool:${evt.call_id}`);
        const who = route.who;
        const q = String(evt.args?.query ?? evt.args?.mapc ?? (evt.args?.mapcs as string[] | undefined)?.join(", ") ?? "");
        this.say("lead", `${CHARACTER_BY_ID[who].name} ơi, ${route.verb.toLowerCase()}${q ? `: "${short(q, 50)}"` : ""}`, 3000);
        this.push(
          who,
          { kind: "say", text: "Vâng, để em đi ngay!", ms: 1500 },
          { kind: "walk", to: route.station, run: true },
          { kind: "work", anim: "search", minMs: 1800, until: token, activity: `${route.verb} — ${route.place}`, facing: route.facing },
          { kind: "walk", to: L.deliver, carry: true },
          { kind: "face", to: { near: "lead" } },
          { kind: "drop", callId: evt.call_id },
          {
            kind: "say",
            text: () => {
              const r = this.results.get(evt.call_id!);
              if (!r || r.count === 0) return "Em chưa tìm thấy gì phù hợp ạ.";
              const first = r.titles.find(Boolean);
              return `Tìm được ${r.count} tài liệu${first ? `, ví dụ "${short(first, 48)}"` : ""}.`;
            },
            ms: 3400,
          },
          { kind: "work", anim: "talk", minMs: 1200, activity: "Bàn giao tài liệu" },
          { kind: "walk", to: CHARACTER_BY_ID[who].work.pos },
        );
        this.push("lead", {
          kind: "work", anim: "think", minMs: 1200, until: token, activity: "Chờ trợ lý tra cứu", seated: true, facing: 0,
        });
        break;
      }

      case "tool_done":
        if (evt.call_id) {
          this.results.set(evt.call_id, { count: evt.count ?? 0, titles: evt.titles ?? [] });
          this.resolve(`tool:${evt.call_id}`);
        }
        break;

      case "compose":
        this.push(
          "lead",
          { kind: "work", anim: "type", minMs: 1500, until: this.expect("answer"), activity: "Soạn câu trả lời", seated: true, facing: 0 },
          { kind: "walk", to: toClient, carry: true },
          { kind: "face", to: { near: "client" } },
          { kind: "say", text: "Đây là ý kiến tư vấn của văn phòng ạ.", ms: 2800 },
          { kind: "work", anim: "talk", minMs: 2000, activity: "Trao đổi với khách" },
          { kind: "walk", to: CHARACTER_BY_ID.lead.work.pos },
        );
        break;
    }
  }
}

export const director = new Director();

/** Resolve Target → toạ độ hiện tại */
export function resolveTarget(t: Target, from: Vec2): Vec2 {
  if (Array.isArray(t)) return t;
  const p = characterPositions.get(t.near);
  if (!p) return from;
  const dist = t.dist ?? 0;
  if (!dist) return [p.x, p.z];
  const dx = from[0] - p.x;
  const dz = from[1] - p.z;
  const d = Math.hypot(dx, dz) || 1;
  return [p.x + (dx / d) * dist, p.z + (dz / d) * dist];
}

export function useOffice() {
  return useSyncExternalStore(director.subscribe, director.getSnapshot, director.getSnapshot);
}

/** Chỉ re-render khi view của đúng nhân vật này đổi */
export function useCharView(id: CharacterId) {
  const get = () => director.getSnapshot().chars[id];
  return useSyncExternalStore(director.subscribe, get, get);
}
function useSnapField<K extends keyof Snapshot>(k: K): Snapshot[K] {
  const get = () => director.getSnapshot()[k];
  return useSyncExternalStore(director.subscribe, get, get);
}
export const useSelected = () => useSnapField("selected");
export const useViewMode = () => useSnapField("view");
export const usePovId = () => useSnapField("povId");
export const useFocus = () => useSnapField("focus");
export const useDialog = () => useSnapField("dialog");
export const usePassages = () => useSnapField("passages");
export const useDeskDocs = () => useSnapField("deskDocs");

// Debug trong môi trường dev: window.__officeDirector
if (typeof window !== "undefined" && process.env.NODE_ENV !== "production") {
  Object.assign(window, { __officeDirector: director });
}
