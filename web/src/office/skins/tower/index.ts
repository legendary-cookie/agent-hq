import type { Skin } from '../../skin';
import { TowerSkin } from './TowerSkin';

export function create(): Skin {
  return new TowerSkin();
}
