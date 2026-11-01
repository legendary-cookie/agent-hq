import type { Skin } from '../../skin';
import { TopdownSkin } from './TopdownSkin';

export function create(): Skin {
  return new TopdownSkin();
}
