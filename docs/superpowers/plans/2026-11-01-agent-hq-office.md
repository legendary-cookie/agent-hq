# Agent HQ Office View (Milestone 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Office page. Every agent is an NPC in a small office:
- isometric by default, with top-down and tower as switchable skins;
- driven by the same live data as the rest of the UI;
- clicking an NPC opens the existing inspector.

**Architecture:**
- **Scene model:** a pure TypeScript module (`web/src/office/scene.ts`). It turns sessions and delegations into rooms, desks, NPCs and walks, and is fully unit-tested.
- **Skins:**
  - Each skin has a pure layout module that maps the scene to screen geometry (positions, hit areas, draw order). These are unit-tested.
  - Each skin has a thin PixiJS renderer that draws that geometry and animates it. It's checked in a real browser, because jsdom has no WebGL or canvas.
- **Office page:** hosts one skin at a time behind a segmented control.

**Tech Stack:** the existing web stack, plus `pixi.js` 8.6.6, pinned exactly. No other new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-30-agent-hq-design.md` section 10 (Office) and section 9 (UI). Visual targets:
- `docs/design/mockup-v2-office.html`: page layout, isometric stage, pills, toast, inspector.
- `docs/design/office-styles.html`: the three skins and the NPC states. The user picked isometric as the default and wants all three available.

## Global Constraints

- **Original art only.** Characters, desks and rooms are drawn in code with PixiJS `Graphics`: simple shapes in the minimal Apple-like style, not copied sprites. There are no bitmap assets, so no licence questions.
- **Colour:**
  - Status colours: working `#34c759`, needs you `#ff9f0a`, error `#ff3b30`, idle `#aeaeb2`.
  - Harness colours: claude_code `#ff9f0a`, codex `#34c759`, antigravity `#5e5ce6`, gemini_cli `#64d2ff`, opencode `#bf5af2`, hermes `#ffd60a`, unknown `#8e8e93`.
  - The floor and walls come from CSS variables, so light and dark mode both work.
- **Performance:**
  - 60 NPCs animate at 60 fps on this PC; check it with the synthetic scene in Task 7.
  - Only one PixiJS `Application` exists at a time, and switching skins destroys the old one.
  - Rendering pauses when the tab is hidden (`document.visibilityState`).
- **Reduced motion:** under `prefers-reduced-motion: reduce`, NPCs jump to their target instead of walking, with no bobbing and no particles.
- **Accessibility:** next to the canvas, a visually hidden list names every NPC with its harness, state and title. Each item is a button that opens the inspector.
- **Data:** the Office uses the existing `useApi` and live refresh. Nothing polls faster than the SSE "changed" notices.
- **Tests:** `npm --prefix web test` and `python -m unittest discover -s tests` must pass after every task. Code is readable, in Prettier style (printWidth 100, single quotes).

## Review Focus

1. **Seats move when the session list changes** (a new session appears, one finishes). Expect existing NPCs to keep their desks; new ones take the first free desk in their room. Test: `desks are stable across updates` (Task 2).
2. **Many agents in one room** (e.g. 25 Codex sessions). Expect the room to grow in rows, with no overlapping desks and no NPC outside its room. Tests: `room grows for 25 desks` (Task 2), `isometric layout has no overlapping desks` (Task 4).
3. **No agents at all, or the API still loading.** Expect a calm empty office ("No agents in the last 24 hours"), not a blank or crashing canvas. Test: `Office shows empty state` (Task 5).
4. **Skin switching and page changes** (leaving Office, switching skins 10 times). Expect no leaked PixiJS apps, WebGL contexts or listeners. Test: `switching skins destroys the previous skin` (Task 5). Browser check in Task 7.
5. **Clicks near or between NPCs.** Expect a click to pick the NPC whose hit area contains the point, preferring the front-most in draw order, or nothing. Tests: `pick prefers front-most NPC`, `pick returns null on empty floor` (Task 4).

---

### Task 1: Delegations endpoint

**Files:**
- Modify: `hq/server.py`
- Test: `tests/test_ui_api.py`

**Interfaces:**
- Produces `GET /api/delegations?limit=20`: newest first, `limit` 1..200.
  - Each row is `{"id": str, "ts": float, "provider": str, "model": str, "task_kind": str, "outcome": str}`.
  - `note` is deliberately left out: it is free text.
  - Same token and Host rules as other routes.

**Steps:**
- [ ] Write the failing tests:
  - `test_delegations_newest_first_and_limited`.
  - `test_delegations_has_no_note`.
  - `test_delegations_requires_token`.
- [ ] FAIL, implement, PASS.
- [ ] Commit `feat(api): delegations feed for the office ticker`.

### Task 2: Scene model

**Files:**
- Create: `web/src/office/scene.ts`, `web/src/office/colors.ts`
- Modify: `web/src/api/types.ts` (add `DelegationRow`, matching Task 1's row shape)
- Test: `web/src/office/scene.test.ts`

**Interfaces (all pure; no DOM or Pixi):**

```ts
export type NpcState = 'working' | 'waiting' | 'idle' | 'error';
export type Room = { id: string; harness: string | null; label: string; capacity: number }; // harness null = break room
export type Desk = { id: string; roomId: string; index: number };                          // index = seat order in room
export type Npc = { id: string; sessionId: string; harness: string; title: string; model: string | null;
                    state: NpcState; color: string; deskId: string | null; targetRoomId: string;
                    parentId: string | null };
export type Walk = { npcId: string; fromRoomId: string; toRoomId: string; startedAt: number };
export type Scene = { rooms: Room[]; desks: Desk[]; npcs: Npc[]; walks: Walk[] };
export type SeatMemory = Map<string, string>; // sessionId -> deskId, owned by the caller between updates
export function buildScene(sessions: SessionRow[], delegations: DelegationRow[], memory: SeatMemory,
                           now: number, prev?: Scene): Scene;
export const ROOM_SIZE_STEP = 6; // desks per row; capacity = ceil(n/6)*6, minimum 6
```

**Rules:**
- **Included sessions:** status other than `done` and `last_event_at` within 24 h.
- **NPC state:**
  - `working` gives working.
  - `waiting` gives waiting (shown as the "needs you" bubble).
  - `error` gives error.
  - `idle` gives idle.
- **Rooms:** one per harness that has included sessions, sorted by harness name, plus a break room (`id 'break'`) that is always last.
- **Desks:** working, waiting and error NPCs sit at a desk in their harness room. Idle NPCs keep their desk reserved but have `targetRoomId = 'break'`.
- **Seat memory:** a session keeps its remembered desk while it is included. New sessions take the lowest free index. Sessions that leave free their desk.
- **Walks:**
  - A walk is created when a session appears whose `parent_id` is an included session: from the parent's room to the child's room.
  - A walk is also created for each delegation newer than `prev`'s newest: from the room of the most recently active `claude_code` NPC to the room of the delegation's provider. Map `codex` to codex, `antigravity` to antigravity, `hermes` to hermes, `opencode` to opencode, and skip anything else.
  - Walks older than 3 s are dropped.
- **Colour:** `colors.ts` exports `HARNESS_COLORS` and `STATUS_COLORS` with the exact values from Global Constraints.

**Steps:**
- [ ] Write the failing tests:
  - `excludes done and stale sessions`.
  - `maps status to npc state`.
  - `one room per harness plus break room last`.
  - `idle npc targets break room but keeps desk`.
  - `desks are stable across updates`.
  - `new session takes lowest free desk`.
  - `room grows for 25 desks` (capacity 30).
  - `subagent creates walk from parent room`.
  - `delegation creates walk to provider room`.
  - `walks expire after 3 s`.
- [ ] FAIL, implement, PASS.
- [ ] Commit `feat(office): scene model`.

### Task 3: Skin interface, Pixi host, shared drawing

**Files:**
- Create:
  - `web/src/office/skin.ts`
  - `web/src/office/PixiStage.tsx`
  - `web/src/office/draw.ts`
  - `web/src/office/motion.ts`
  - `web/src/office/skins/index.ts`
  - `web/src/office/pick.ts`
- Modify: `web/package.json` (add `pixi.js` 8.6.6 exactly)
- Test: `web/src/office/motion.test.ts`, `web/src/office/skins/index.test.ts`, `web/src/office/pick.test.ts`

**Interfaces:**

```ts
// skin.ts
export type SkinId = 'isometric' | 'topdown' | 'tower';
export type Point = { x: number; y: number };
export interface Skin {
  mount(host: HTMLElement): Promise<void>;
  render(scene: Scene, dt: number, theme: Theme): void;   // dt in seconds
  pick(x: number, y: number): string | null;              // npc id, canvas CSS pixels
  resize(width: number, height: number): void;
  destroy(): void;
}
export type Theme = { floor: string; floorAlt: string; wall: string; text: string; muted: string;
                      reducedMotion: boolean };
// Every skin's pure layout function returns this shape.
export type SkinLayout = { rooms: { id: string; label: string; labelAt: Point; shape: Point[] }[]; // polygon
                           desks: { id: string; at: Point; depth: number }[];
                           seats: Map<string, Point>;           // deskId -> NPC seat point
                           breakSpots: Point[];                 // idle NPC spots in the break room
                           bounds: { width: number; height: number } };
// pick.ts (pure, shared by all skins)
export function npcHitArea(at: Point): { x: number; y: number; w: number; h: number }; // 24x40 around feet
export function pickNpc(npcs: { id: string; at: Point; depth: number }[], x: number, y: number): string | null;
// skins/index.ts: skins load lazily so PixiJS never loads in tests or before the Office opens
export const SKINS: Record<SkinId, { label: string; create: () => Promise<Skin> }>; // create = dynamic import()
export function parseSkin(value: string | null | undefined, fallback: SkinId): SkinId;
// motion.ts (pure)
export function approach(current: Point, target: Point, speed: number, dt: number): Point; // no overshoot
export function bob(t: number, phase: number): number;          // typing bob offset in px, |v| <= 2
export function readTheme(el: HTMLElement): Theme;              // reads CSS vars + matchMedia
```

- `PixiStage.tsx`, signature `PixiStage({ skin: SkinId; scene: Scene; selectedId: string | null; onPick(npcId: string): void; skinFactory?: (id: SkinId) => Promise<Skin> })`:
  - Creates the skin, mounts it into a div, and runs `requestAnimationFrame`, calling `render(scene, dt)`. The loop pauses while the tab is hidden.
  - Forwards clicks to `pick`, and handles resize with a `ResizeObserver`.
  - Destroys the skin on unmount or when the skin changes.
- `draw.ts` holds the shared PixiJS `Graphics` helpers used by all skins:
  - `drawPerson(g, color, state, t)`: head, body, and status ring. A "!" bubble when waiting, a red puff when error.
  - `drawDesk(g)`.
  - `drawLabel(container, text)`.

**Steps:**
- [ ] Write the failing tests:
  - `approach never overshoots`.
  - `bob stays within 2 px`.
  - `parseSkin falls back for unknown values`.
  - `SKINS has three skins`.
  - `pick prefers front-most NPC` (higher depth wins).
  - `pick returns null on empty floor`.
- [ ] FAIL, implement, PASS. Install with `npm --prefix web install --save-exact pixi.js@8.6.6`.
- [ ] Commit `feat(office): skin interface, pixi host, drawing helpers`.

### Task 4: Isometric skin (default)

**Files:**
- Create: `web/src/office/skins/isometric/layout.ts`, `web/src/office/skins/isometric/IsometricSkin.ts`
- Test: `web/src/office/skins/isometric/layout.test.ts`

**Interfaces:**

```ts
export const TILE_W = 64, TILE_H = 32;
export function isoToScreen(gx: number, gy: number, origin: Point): Point; // x=(gx-gy)*TILE_W/2, y=(gx+gy)*TILE_H/2
export function layoutIsometric(scene: Scene, viewport: { width: number; height: number }): SkinLayout;
// Room shapes are the zones' outer diamonds; the renderer draws tiles inside them. Picking uses pick.ts.
```

**Layout rules:**
- One floor with a 2-column grid of zones, ordered as the scene's rooms. Each zone is `cols=6` by `rows=ceil(capacity/6)*2` tiles, and desks sit on even rows.
- The break room is a zone with 8 spots.
- The whole floor is scaled to fit the viewport, with a 24 px margin, and centred. The scale is clamped between 0.5 and 1.5.
- Depth for draw order is `gx + gy`.

**Renderer:**
- Floor diamonds in alternating `floor` and `floorAlt` colours, a thin wall edge on the two back sides, and zone labels.
- Desks are iso boxes. NPCs are drawn with `drawPerson` and move with `approach` (speed 120 px/s) from their current point to their seat or break spot.
- Walks: a dashed path that fades over 3 s from the parent's or source room's label to the target seat.
- **Hover:** the hovered NPC grows to 1.1× and its title appears in a small pill above the head.
- **Selection:** a blue `#0071e3` ring around the selected NPC.

**Steps:**
- [ ] Write the failing tests:
  - `isoToScreen matches formula`.
  - `zones do not overlap`.
  - `isometric layout has no overlapping desks` (6 rooms × 25 desks).
  - `layout fits viewport`.
- [ ] FAIL, implement, PASS.
- [ ] Commit `feat(office): isometric skin`.

### Task 5: Office page

**Files:**
- Create: `web/src/pages/Office.tsx`, `web/src/pages/office.css`
- Modify:
  - `web/src/app/router.ts`: add `'office'`, which becomes the default route.
  - `web/src/app/App.tsx`
  - `web/src/components/Sidebar.tsx`: enable Office and remove "Soon".
  - `web/src/app/token.ts`: post-bootstrap hash becomes `#/office`.
- Test: `web/src/pages/Office.test.tsx`, plus updated router and token tests.

**Layout, matching the mockup:**
- **Header:** title "Office" and a segmented skin control (Isometric, Top-down, Tower).
  - The skin is saved in `localStorage['hq-skin']`.
  - The default comes from `/api/settings` `default_skin`, run through `parseSkin`.
  - Wrap `localStorage` access in try/catch.
- **Pills:** working, needs you and idle counts, best now, and today's tokens and value, as on the Dashboard. Reuse its helpers by extracting them to `web/src/components/StatusPills.tsx`.
- **Filters:** harness and account selects that filter which NPCs appear.
- **Stage:** a card holding `PixiStage`, with a minimum height of 420 px that fills the available height. Below it, the visually hidden accessible NPC list.
- **Ticker toast,** bottom-left of the stage: the newest delegation within the last 60 s, worded "14:02 · delegated implementation to codex · gpt-6.1-terra". It fades after 6 s.
- **Inspector:** reuses `Inspector` on NPC click and keeps the selection while the data refreshes.
- **Empty state:** "No agents in the last 24 hours".

**Testing:** inject the skin factory so tests use a fake skin that records its calls. Signature: `Office({ skinFactory? }: { skinFactory?: (id: SkinId) => Promise<Skin> })`. The default is `SKINS[id].create`, and `PixiStage` takes the factory too.

**Steps:**
- [ ] Write the failing tests:
  - `Office is the default route`.
  - `Office shows empty state`.
  - `Office renders accessible npc list`.
  - `switching skins destroys the previous skin`.
  - `skin choice persists in localStorage`.
  - `clicking accessible npc opens inspector`.
  - `ticker shows newest recent delegation`.
- [ ] FAIL, implement, PASS.
- [ ] Commit `feat(web): office page`.

### Task 6: Top-down and tower skins

**Files:**
- Create:
  - `web/src/office/skins/topdown/layout.ts`
  - `web/src/office/skins/topdown/TopdownSkin.ts`
  - `web/src/office/skins/tower/layout.ts`
  - `web/src/office/skins/tower/TowerSkin.ts`
- Test: `web/src/office/skins/topdown/layout.test.ts`, `web/src/office/skins/tower/layout.test.ts`

**Rules:**
- **Top-down:**
  - A 2-column grid of square rooms.
  - Desks in rows of 6 inside each room.
  - Break room last with a coffee table.
  - Pixel style: integer coordinates, square shapes, 2 px outlines.
- **Tower:**
  - One floor per harness, stacked top to bottom in scene room order, with the break room as the ground floor.
  - Desks along the floor, 6 per row. Extra rows grow the floor's height.
  - A lift shaft on the right. Cross-room walks travel desk → lift → target floor → desk, shown as an NPC moving along that path.
- They export `layoutTopdown(scene, viewport): SkinLayout` and `layoutTower(scene, viewport): SkinLayout`, and pick with the shared `pickNpc` from `pick.ts`.
- `layoutTower` also exports `towerWalkPath(layout, fromRoomId, toRoomId): Point[]`.

**Steps:**
- [ ] Write the failing tests for each skin:
  - `rooms do not overlap`.
  - `desks inside their room`.
  - `layout fits viewport`.
  - tower only: `tower walk path goes through lift`.
- [ ] FAIL, implement, PASS.
- [ ] Commit `feat(office): top-down and tower skins`.

### Task 7: Verify on real data, performance, docs

**Steps:**
- [ ] `npm --prefix web ci && npm --prefix web run build`, then both test suites. Expected: all pass.
- [ ] Run `python -m hq --db <real db copy> --no-browser` from PowerShell and open the printed link in a browser. For each of the three skins, in light and in dark mode, at 1280 and at 1024 px:
  - NPCs appear in the right rooms.
  - Clicking one opens the right inspector session.
  - Hovering shows the title.
  - No console errors.
- [ ] **Performance:** a dev-only query flag `?synthetic=60` (behind `import.meta.env.DEV`) feeds 60 synthetic NPCs. Measure fps over 5 s with `requestAnimationFrame` timestamps. Expected: ≥ 55 fps for each skin.
- [ ] **Leaks:** switch skins 10 times, then confirm that `document.querySelectorAll('canvas').length === 1` and that the JS heap is stable within 10 MB (`performance.memory` where available).
- [ ] **Reduced motion:** emulate `prefers-reduced-motion` and confirm NPCs don't walk.
- [ ] **Docs:**
  - README: the Office section and the skins.
  - CHANGELOG `0.3.0`, with the checks above and the measured fps.
- [ ] Commit `feat: office view (milestone 3)`.
