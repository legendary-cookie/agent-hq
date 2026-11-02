import { expect, it, vi } from 'vitest';
const { apps } = vi.hoisted(() => ({ apps: [] as any[] }));
vi.mock('pixi.js', () => {
  class Container {
    addChild() {}
  }
  class Graphics extends Container {}
  class Text extends Container {}
  class Application {
    renderer = { resize: vi.fn() };
    destroy = vi.fn(() => {
      throw new Error('not initialized');
    });
    constructor() {
      apps.push(this);
    }
    async init() {}
  }
  return { Application, Container, Graphics, Text };
});
import { RetainedSkin, hoverTitle } from './retained';
it('destroy before mount does not throw', () => {
  const skin = new RetainedSkin('tower');
  expect(() => skin.destroy()).not.toThrow();
});
it('resize updates renderer dimensions', async () => {
  const skin = new RetainedSkin('tower');
  const app = apps[apps.length - 1];
  app.canvas = document.createElement('canvas');
  app.stage = { addChild: vi.fn() };
  await skin.mount(document.createElement('div'));
  skin.resize(321, 456);
  expect(app.renderer.resize).toHaveBeenCalledWith(321, 456);
});
it('hover title is truncated', () => {
  expect(hoverTitle('x'.repeat(81))).toBe(`${'x'.repeat(80)}…`);
  expect(hoverTitle('short')).toBe('short');
});
