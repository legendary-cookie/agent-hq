import { describe, expect, it } from 'vitest';
import { DEFAULTS, computeRooms, feedText, homeRoom, limited, place, timeOfDay, words } from './model';
import type { Activity, OfficeAgent, OfficeData, Settings } from './model';

const NOW = 1000000;
function agent(host: string, patch: Partial<OfficeAgent> = {}): OfficeAgent {
  return { host, label: host.toUpperCase(), owner: null, group: 'Staff', department: null, status: 'idle', error: null, catching_up: false, last_seen: NOW - 5000, activity: null, earlier: [], ...patch };
}
const office = (agents: OfficeAgent[], meetings: OfficeData['meetings'] = []): OfficeData => ({ now: NOW, agents, exchanges: [], meetings, feed: [] });
const settings = (patch: Partial<Settings> = {}): Settings => ({ ...DEFAULTS, ...patch });
const work = (kind: Activity['kind'], title: string | null = 'Weekly report', at = NOW - 10): Activity => ({ kind, title, at });
const one = (data: OfficeData, s = settings()) => place(data, s)[0];

describe('place', () => {
  it('keeps working agents at their desks with their real task', () => {
    const a = one(office([agent('a', { status: 'working', activity: work('search') })]));
    expect([a.where, a.shown, a.task]).toEqual(['desk', 'working', { kind: 'search', title: 'Weekly report' }]);
  });
  it('gives idle and quiet agents no task', () => {
    expect(one(office([agent('a')])).task).toBeNull();
    expect(one(office([agent('a', { status: 'quiet' })])).shown).toBe('quiet');
  });
  it('sends agents who just finished to the lounge, newest first, up to seven', () => {
    const agents = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'].map((host, i) => agent(host, { status: 'recent', last_seen: NOW - i * 10 }));
    const placed = place(office(agents), settings());
    expect(placed.filter((a) => a.where === 'lounge').map((a) => a.host)).toEqual(['a', 'b', 'c', 'd', 'e', 'f', 'g']);
    expect(placed[7]).toMatchObject({ where: 'desk', shown: 'idle' });
  });
  it('keeps them at their desks if the lounge is hidden', () => {
    expect(one(office([agent('a', { status: 'recent' })]), settings({ hidden: { lounge: true } })).where).toBe('desk');
  });
  it('only meets when the data says so', () => {
    const agents = [agent('a', { status: 'working', activity: work('read') }), agent('b')];
    expect(place(office(agents), settings()).some((a) => a.where === 'meeting')).toBe(false);
    const placed = place(office(agents, [{ hosts: ['a', 'b'], count: 3, since: NOW - 60, last: NOW - 5 }]), settings());
    expect(placed.map((a) => a.where)).toEqual(['meeting', 'meeting']);
    expect(placed[0].meetingWith).toEqual(['b']);
  });
  it('leaves an unreachable agent out of a meeting', () => {
    const placed = place(office([agent('a', { status: 'attention' }), agent('b')], [{ hosts: ['a', 'b'], count: 3, since: NOW - 60, last: NOW - 5 }]), settings());
    expect(placed.map((a) => a.where)).toEqual(['desk', 'desk']);
    expect(placed[0].shown).toBe('attention');
  });
  it('goes to the archives only for a Google Drive tool', () => {
    expect(one(office([agent('a', { status: 'working', activity: work('drive') })])).where).toBe('archive');
    expect(one(office([agent('a', { status: 'working', activity: work('drive') })]), settings({ hidden: { archives: true } }))).toMatchObject({ where: 'desk', task: { kind: 'read' } });
  });
  it('counts a message just now as work, though messages alone don’t make a session', () => {
    expect(one(office([agent('a', { status: 'recent', activity: work('message', null, NOW - 20) })])).shown).toBe('working');
    expect(one(office([agent('a', { status: 'recent', activity: work('message', null, NOW - 500) })])).shown).toBe('break');
  });
});

describe('words', () => {
  const working = one(office([agent('a', { status: 'working', activity: work('draft') })]));
  it('shows the title in full and only the type of work in privacy', () => {
    expect(words(working, settings())).toBe('Weekly report');
    expect(words(working, settings({ detail: 'privacy' }))).toBe('Drafting a document');
  });
  it('never leaks a title in privacy', () => {
    const data = office([agent('a', { status: 'working', activity: work('drive', 'Secret plan') })]);
    expect(words(one(data), settings({ detail: 'privacy' }))).not.toContain('Secret');
    const feed = feedText({ at: NOW, type: 'activity', host: 'a', kind: 'read', title: 'Secret plan', between_agents: false }, (h) => h, settings({ detail: 'privacy' }));
    expect(feed).toBe('a: reading files');
  });
  it('names who is in the meeting, except in privacy', () => {
    const placed = place(office([agent('a'), agent('b')], [{ hosts: ['a', 'b'], count: 3, since: NOW, last: NOW }]), settings());
    expect(words(placed[0], settings(), new Map([['b', 'Bea']]))).toBe('Talking with Bea');
    expect(words(placed[0], settings({ detail: 'privacy' }))).toBe('In a meeting');
  });
});

describe('trouble and time', () => {
  it('is limited only while no work has followed the limit message', () => {
    expect(limited({ limit_at: NOW - 600, activity: work('think', null, NOW - 610) })).toBe(true);
    expect(limited({ limit_at: NOW - 600, activity: work('read', null, NOW - 10) })).toBe(false);
    expect(limited({ limit_at: null, activity: null })).toBe(false);
    expect(limited({ limit_at: NOW - 600, activity: work('message', null, NOW - 5), last_from_person: NOW - 5 })).toBe(true);
  });
  it('says so in the bubble', () => {
    const a = one(office([agent('a', { status: 'working', activity: work('think', null, NOW - 700), limit_at: NOW - 600 })]));
    expect(words(a, settings())).toBe('Waiting: hit a usage limit');
  });
  it('follows the clock on auto', () => {
    const at = (hour: number) => new Date(2026, 10, 6, hour, 30);
    expect([timeOfDay('auto', at(9)), timeOfDay('auto', at(17)), timeOfDay('auto', at(22)), timeOfDay('auto', at(3))]).toEqual(['day', 'golden', 'night', 'night']);
    expect(timeOfDay('night', at(9))).toBe('night');
  });
});

describe('rooms', () => {
  const fleet = [agent('m', { group: 'Management' }), agent('s'), agent('s2'), agent('d', { group: 'Department', label: 'Customer Insights' })];
  it('seats people by their group', () => {
    const s = settings();
    expect(fleet.map((a) => homeRoom(a, s))).toEqual(['mgmt', 'staff', 'staff', 'd-customer-insights']);
  });
  it('gives every agent exactly one desk', () => {
    const rooms = computeRooms(fleet, settings());
    const owners = Object.values(rooms).flatMap((room) => room.desks.map((desk) => desk.owner)).filter(Boolean);
    expect(owners.sort()).toEqual(['d', 'm', 's', 's2']);
  });
  it('a dragged agent stays where it was put, and an unknown room falls back to a real desk', () => {
    const moved = computeRooms(fleet, settings({ agentRoom: { s: 'd-customer-insights' } }));
    expect(moved['d-customer-insights'].desks.map((desk) => desk.owner).sort()).toEqual(['d', 's']);
    const lost = computeRooms(fleet, settings({ agentRoom: { s: 'gone' } }));
    expect(Object.values(lost).flatMap((room) => room.desks.map((desk) => desk.owner))).toContain('s');
  });
  it('hiding a room removes it, but Management always stays', () => {
    const rooms = computeRooms(fleet, settings({ hidden: { lounge: true, mgmt: true } }));
    expect(rooms.lounge).toBeUndefined();
    expect(rooms.mgmt).toBeDefined();
  });
  it('seats staff with a department only when asked', () => {
    const staff = agent('s', { department: 'Sales' });
    expect(homeRoom(staff, settings())).toBe('staff');
    expect(homeRoom(staff, settings({ seating: 'departments' }))).toBe('d-sales');
  });
});
