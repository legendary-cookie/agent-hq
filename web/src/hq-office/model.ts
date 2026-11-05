/** The Office's data, choices and layout. Nothing here draws, so it all runs in tests. */

export type Kind = 'search' | 'draft' | 'read' | 'orders' | 'message' | 'schedule' | 'command' | 'think' | 'drive';
export type Where = 'desk' | 'meeting' | 'lounge' | 'archive';
export type Status = 'working' | 'idle' | 'quiet' | 'attention' | 'meeting' | 'break' | 'archive';

export interface Activity { kind: Kind; tool?: string | null; title: string | null; at: number; between_agents?: boolean }
export interface OfficeAgent {
  host: string;
  label: string;
  owner: string | null;
  group: string;
  department: string | null;
  status: 'working' | 'recent' | 'idle' | 'quiet' | 'attention';
  error: string | null;
  catching_up: boolean;
  last_seen: number | null;
  activity: Activity | null;
  earlier: { kind: Kind; title: string | null; at: number }[];
  last_from_person?: number | null;
  limit_at?: number | null;
  api_error_at?: number | null;
}

/** Still held up by a usage limit: a limit message with no work after it. A person's message
 *  arriving doesn't count as work, since a limited agent can't answer it. */
export const limited = (a: Pick<OfficeAgent, 'limit_at' | 'activity'> & { last_from_person?: number | null }) => {
  if (!a.limit_at) return false;
  const act = a.activity;
  if (!act || act.at <= a.limit_at + 60) return true;
  return act.kind === 'message' && !!a.last_from_person && Math.abs(act.at - a.last_from_person) < 1;
};

/** The light the office should have: the chosen one, or by the real clock for "auto". */
export function timeOfDay(choice: Settings['time'], date = new Date()): 'day' | 'golden' | 'night' {
  if (choice !== 'auto') return choice;
  const hour = date.getHours();
  return hour >= 7 && hour < 17 ? 'day' : hour >= 17 && hour < 19 ? 'golden' : 'night';
}
export interface Exchange { id: number; from: string; to: string; at: number }
export interface Meeting { hosts: string[]; count: number; since: number; last: number }
export type FeedEntry =
  | { at: number; type: 'activity'; host: string; kind: Kind; title: string | null; between_agents: boolean }
  | { at: number; type: 'exchange'; host: string; to: string }
  | { at: number; type: 'meeting'; hosts: string[] };
export interface OfficeData { now: number; agents: OfficeAgent[]; exchanges: Exchange[]; meetings: Meeting[]; feed: FeedEntry[] }

export interface Settings {
  view: '3d' | 'flat';
  theme: string;
  flatStyle: string;
  character: string;
  detail: 'full' | 'privacy';
  time: 'auto' | 'day' | 'golden' | 'night';
  seating: 'staff' | 'departments';
  names: boolean;
  roomLabels: boolean;
  bubbles: 'busy' | 'all' | 'off';
  walk: boolean;
  naps: boolean;
  wallHeight: 'low' | 'mid' | 'high';
  hidden: Record<string, boolean>;
  roomNames: Record<string, string>;
  roomColors: Record<string, string>;
  agentRoom: Record<string, string>;
  agentColors: Record<string, string>;
  agentNames: Record<string, string>;
  agentChars: Record<string, string>;
}
export const DEFAULTS: Settings = {
  view: '3d', theme: 'toybox', flatStyle: 'cute', character: 'blob', detail: 'full', time: 'auto', seating: 'staff', names: true, roomLabels: true,
  bubbles: 'busy', walk: true, naps: true, wallHeight: 'low', hidden: {}, roomNames: {}, roomColors: {}, agentRoom: {}, agentColors: {}, agentNames: {}, agentChars: {},
};
const KEY = 'hq-office';
export function loadSettings(): Settings {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || '{}') }; } catch { return { ...DEFAULTS }; }
}
export function saveSettings(settings: Settings) {
  try { localStorage.setItem(KEY, JSON.stringify(settings)); } catch { /* Choices are optional. */ }
}

export const WORK: Record<Kind, string> = {
  search: 'Searching the web',
  draft: 'Drafting a document',
  read: 'Reading files',
  orders: 'Checking orders',
  message: 'Replying to a message',
  schedule: 'Running a scheduled task',
  command: 'Running commands',
  think: 'Thinking',
  drive: 'Getting files from Google Drive',
};
export const STATUS_TEXT: Record<Status, string> = { working: 'Working', idle: 'Idle', quiet: 'Quiet', attention: 'Needs help', meeting: 'In a meeting', break: 'On a break', archive: 'In the archives' };
export const CHARACTERS: Record<string, string> = { blob: 'Blobs', bot: 'Robots', person: 'Little people', critter: 'Critters', mix: 'A mix of all' };

/** One agent as the office should show it now. */
export interface Placed extends OfficeAgent {
  index: number;
  where: Where;
  shown: Status;
  task: { kind: Kind; title: string | null } | null;
  meetingWith: string[];
}
const RECENT_MESSAGE_S = 90;
// The lounge and archives have free standing room, so they hold more than their old fixed spots.
const SEATS = { meeting: 6, lounge: 7, archive: 4 };

export function place(data: OfficeData, settings: Settings): Placed[] {
  const inMeeting = new Map<string, Meeting>();
  if (!settings.hidden.meet) {
    let free = SEATS.meeting;
    for (const meeting of data.meetings) {
      const reachable = meeting.hosts.filter((host) => data.agents.find((a) => a.host === host)?.status !== 'attention');
      if (reachable.length < 2 || reachable.length > free) continue;
      free -= reachable.length;
      for (const host of reachable) inMeeting.set(host, meeting);
    }
  }
  const loungers = settings.hidden.lounge ? [] : data.agents
    .filter((a) => a.status === 'recent' && !inMeeting.has(a.host) && !isMessaging(a, data.now))
    .sort((a, b) => (b.last_seen ?? 0) - (a.last_seen ?? 0)).slice(0, SEATS.lounge).map((a) => a.host);
  let archiveFree = settings.hidden.archives ? 0 : SEATS.archive;
  return data.agents.map((agent, index) => {
    const base = { ...agent, index, meetingWith: [] as string[] };
    const meeting = inMeeting.get(agent.host);
    if (meeting) return { ...base, where: 'meeting' as const, shown: 'meeting' as const, task: null, meetingWith: meeting.hosts.filter((h) => h !== agent.host) };
    if (agent.status === 'attention') return { ...base, where: 'desk' as const, shown: 'attention' as const, task: null };
    if (agent.status === 'quiet') return { ...base, where: 'desk' as const, shown: 'quiet' as const, task: null };
    if (loungers.includes(agent.host)) return { ...base, where: 'lounge' as const, shown: 'break' as const, task: null };
    if (agent.status === 'working' || isMessaging(agent, data.now)) {
      const kind = agent.activity?.kind ?? 'think';
      if (kind === 'drive' && archiveFree > 0) {
        archiveFree -= 1;
        return { ...base, where: 'archive' as const, shown: 'archive' as const, task: { kind, title: agent.activity?.title ?? null } };
      }
      return { ...base, where: 'desk' as const, shown: 'working' as const, task: { kind: kind === 'drive' ? 'read' : kind, title: agent.activity?.title ?? null } };
    }
    return { ...base, where: 'desk' as const, shown: 'idle' as const, task: null };
  });
}
function isMessaging(agent: OfficeAgent, now: number) {
  const a = agent.activity;
  return !!a && (a.kind === 'message' || a.kind === 'schedule') && now - a.at < RECENT_MESSAGE_S;
}

export const displayName = (a: Pick<Placed, 'host' | 'label' | 'index'>, s: Settings) => (s.names ? (s.agentNames[a.host] || a.label) : `Agent ${a.index + 1}`);

export function words(a: Placed, s: Settings, names: Map<string, string> = new Map()): string {
  const secret = s.detail === 'privacy';
  if (a.where === 'meeting') {
    if (secret) return 'In a meeting';
    const others = a.meetingWith.map((host) => names.get(host) ?? host);
    return others.length ? `Talking with ${others.join(', ')}` : 'In a meeting';
  }
  if (a.shown === 'attention') return secret ? 'Needs help' : 'HQ can’t reach this agent';
  if (limited(a)) return 'Waiting: hit a usage limit';
  if (a.where === 'archive') return secret ? 'Getting a file' : (a.task?.title ?? WORK.drive);
  if (a.where === 'lounge') return 'On a break';
  if (a.shown === 'quiet') return 'Zzz…';
  if (!a.task) return 'Waiting for work';
  if (a.task.kind === 'message' && a.activity?.between_agents) return 'Talking to another agent';
  return secret ? WORK[a.task.kind] : (a.task.title || WORK[a.task.kind]);
}

export function feedText(entry: FeedEntry, name: (host: string) => string, s: Settings): string {
  const secret = s.detail === 'privacy';
  if (entry.type === 'exchange') return `${name(entry.host)} messaged ${name(entry.to)}`;
  if (entry.type === 'meeting') {
    const names = entry.hosts.map(name);
    return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]} are talking`;
  }
  if (entry.between_agents) return `${name(entry.host)}: talking to another agent`;
  if (secret || !entry.title) return `${name(entry.host)}: ${WORK[entry.kind].charAt(0).toLowerCase()}${WORK[entry.kind].slice(1)}`;
  return `${name(entry.host)}: ${entry.title}`;
}

// Layout
export interface Room {
  id: string; name: string; base: string; x0: number; z0: number; w: number; d: number; tint: number;
  top?: boolean; dept?: boolean; doorX: number;
  desks: { x: number; z: number; owner: string | null }[];
}
export const CORRIDOR_Z = -4.5;
const TOP: [string, string, number][] = [['mgmt', 'Management', 13], ['meet', 'Meeting room', 11], ['lounge', 'Lounge', 8], ['archives', 'Archives · Google Drive', 8.5], ['systems', 'Systems', 4.5]];
export const topRoomName = (id: string) => TOP.find((room) => room[0] === id)?.[1] ?? id;
const slug = (text: string) => text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

export function homeRoom(a: Pick<OfficeAgent, 'host' | 'group' | 'department' | 'label'>, s: Settings): string {
  const chosen = s.agentRoom[a.host];
  if (chosen) return chosen;
  if (a.group === 'Management') return 'mgmt';
  if (a.group === 'Department') return `d-${slug(a.department || a.label)}`;
  if (s.seating === 'departments' && a.department) return `d-${slug(a.department)}`;
  return 'staff';
}

/** Whether any agent has a department that its room could be named after. */
export const canSeatByDepartment = (agents: OfficeAgent[]) => agents.some((a) => a.group !== 'Department' && a.group !== 'Management' && a.department);

export function computeRooms(agents: OfficeAgent[], s: Settings): Record<string, Room> {
  const out: Record<string, Room> = {};
  const name = (id: string, fallback: string) => s.roomNames[id] || fallback;
  const top = TOP.filter(([id]) => id === 'mgmt' || !s.hidden[id]);
  const stretch = (48 - (top.length - 1)) / top.reduce((sum, r) => sum + r[2], 0);
  let x = -24;
  top.forEach(([id, base, w], i) => {
    const width = w * stretch;
    out[id] = { id, name: name(id, base), base, x0: x, z0: -16, w: width, d: 10, top: true, tint: i, doorX: x + width / 2, desks: [] };
    x += width + 1;
  });
  const depts = new Map<string, string>();
  const homes = agents.map((a) => ({ agent: a, home: homeRoom(a, s) }));
  for (const { agent, home } of homes) if (home.startsWith('d-')) depts.set(home, agent.department || (agent.group === 'Department' ? agent.label : home.slice(2)));
  for (const agent of agents) if (agent.group === 'Department') depts.set(`d-${slug(agent.department || agent.label)}`, agent.department || agent.label);
  const wantStaff = !depts.size || homes.some(({ home }) => home === 'staff');
  const areaX = wantStaff ? 0 : -24;
  const cols = !depts.size ? 1 : wantStaff ? 3 : 4;
  const rows = Math.max(1, Math.ceil(depts.size / cols));
  if (wantStaff) {
    const w = depts.size ? 23 : 48;
    out.staff = { id: 'staff', name: name('staff', 'Staff room'), base: 'Staff room', x0: -24, z0: -3, w, d: 18, tint: 4, doorX: -24 + w / 2, desks: [] };
  }
  const cellW = ((24 - areaX) - (cols - 1)) / cols;
  const cellD = (18 - (rows - 1)) / rows;
  [...depts].sort((a, b) => a[1].localeCompare(b[1])).forEach(([id, base], i) => {
    const col = i % cols, row = Math.floor(i / cols);
    const x0 = areaX + col * (cellW + 1);
    out[id] = { id, name: name(id, base), base, x0, z0: -3 + row * (cellD + 1), w: cellW, d: cellD, tint: 5 + (i % 2), dept: true, doorX: x0 + cellW / 2, desks: [] };
  });
  for (const room of Object.values(out)) {
    if (room.id !== 'mgmt' && room.id !== 'staff' && !room.dept) continue;
    const here = homes.filter(({ home }) => home === room.id).map(({ agent }) => agent.host);
    const perRow = Math.max(1, Math.floor((room.w - 1.2) / 2.75));
    const rowsNeeded = Math.max(1, Math.ceil(here.length / perRow));
    const startZ = room.z0 + (room.dept ? room.d * .36 : 2.6);
    const gapZ = rowsNeeded > 1 ? Math.min(3.3, (room.d - 3.5) / (rowsNeeded - 1)) : 0;
    const spare = room.dept ? 0 : perRow * rowsNeeded - here.length;
    for (let i = 0; i < here.length + spare; i += 1) {
      const col = i % perRow;
      const rowCount = room.dept ? Math.min(perRow, here.length - Math.floor(i / perRow) * perRow) : perRow;
      const spacing = room.w / (rowCount + .0001);
      room.desks.push({ x: room.x0 + spacing * (col + .5), z: startZ + Math.floor(i / perRow) * gapZ, owner: here[i] ?? null });
    }
  }
  // Anyone sent to a room that's gone sits in the staff room's next spare spot.
  const seated = new Set(Object.values(out).flatMap((room) => room.desks.map((desk) => desk.owner)));
  const fallback = out.staff ?? out.mgmt;
  for (const { agent } of homes) {
    if (seated.has(agent.host)) continue;
    const spare = fallback.desks.find((desk) => !desk.owner);
    if (spare) spare.owner = agent.host;
    else fallback.desks.push({ x: fallback.x0 + 1.4 + fallback.desks.length * .4, z: fallback.z0 + 2.6, owner: agent.host });
  }
  return out;
}
