import { useEffect, useRef, useState } from 'react';
import { readTheme } from './motion';
import type { Scene } from './scene';
import type { Skin, SkinId } from './skin';
import { SKINS } from './skins';

type Props = {
  skin: SkinId;
  scene: Scene;
  selectedId: string | null;
  onPick(npcId: string): void;
  skinFactory?: (id: SkinId) => Promise<Skin>;
};
export function PixiStage({ skin, scene, selectedId, onPick, skinFactory }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  const current = useRef({ scene, selectedId, onPick });
  current.current = { scene, selectedId, onPick };
  useEffect(() => {
    const element = host.current;
    if (!element) return;
    setFailed(false);
    let active = true;
    let instance: Skin | null = null;
    let mounting = false;
    let frame = 0;
    let previous: number | null = null;
    const resize = () => instance?.resize(element.clientWidth, element.clientHeight);
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    const click = (event: MouseEvent) => {
      const rect = element.getBoundingClientRect();
      const id = instance?.pick(event.clientX - rect.left, event.clientY - rect.top);
      if (id) current.current.onPick(id);
    };
    const tick = (time: number) => {
      if (!active || document.visibilityState === 'hidden') return;
      const dt = previous === null ? 0 : (time - previous) / 1000;
      instance?.setSelected?.(current.current.selectedId);
      instance?.render(current.current.scene, dt, readTheme(element));
      previous = time;
      frame = requestAnimationFrame(tick);
    };
    const visibility = () => {
      cancelAnimationFrame(frame);
      previous = null;
      if (active && instance && !mounting && document.visibilityState !== 'hidden') {
        frame = requestAnimationFrame(tick);
      }
    };
    document.addEventListener('visibilitychange', visibility);
    element.addEventListener('click', click);
    (skinFactory ? skinFactory(skin) : SKINS[skin].create())
      .then(async (created) => {
        if (!active) {
          created.destroy();
          return;
        }
        instance = created;
        mounting = true;
        await created.mount(element);
        mounting = false;
        if (!active) {
          created.destroy();
          return;
        }
        resize();
        visibility();
      })
      .catch(() => {
        mounting = false;
        instance?.destroy();
        instance = null;
        if (active) setFailed(true);
      });
    return () => {
      active = false;
      cancelAnimationFrame(frame);
      observer.disconnect();
      element.removeEventListener('click', click);
      document.removeEventListener('visibilitychange', visibility);
      if (!mounting) instance?.destroy();
    };
  }, [skin, skinFactory]);
  return (
    <div ref={host} className="office-canvas">
      {failed && (
        <div className="office-start-error" role="alert">
          The office view could not start (WebGL unavailable).
        </div>
      )}
    </div>
  );
}
