import type { Skin } from '../skin';
// Replaced by the individual renderers in the following tasks.
export function create(): Skin {
  return {
    async mount() {},
    render() {},
    pick() {
      return null;
    },
    resize() {},
    destroy() {},
  };
}
