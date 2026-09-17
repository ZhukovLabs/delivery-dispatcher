"use client";

import type { MutableRefObject } from "react";
import { ANIM_MS, nearestIdxFrom } from "./mapUtils";
import type { AnimMap, LayerState } from "./mapLayers";

/* ---------- плавное движение курьеров (rAF-интерполяция) ---------- */
// маркер едет к свежей геопозиции, и линия его маршрута подрезается тем же
// кадром: хвост «пройденного» тает непрерывно, как в навигаторах
export function makeEnsureRaf(deps: {
  anims: MutableRefObject<AnimMap>;
  L: MutableRefObject<LayerState>;
  selCid: MutableRefObject<string | null>;
  rafRef: MutableRefObject<number>;
}) {
  const { anims, L, selCid, rafRef } = deps;
  return function ensureRaf() {
    if (rafRef.current) return;
    const step = () => {
      const t0 = performance.now();
      let alive = false;
      anims.current.forEach((a, cid) => {
        const t = Math.min(1, (t0 - a.start) / ANIM_MS);
        const e = t < .5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
        const ip: [number, number] = [a.from[0] + (a.to[0] - a.from[0]) * e,
                                      a.from[1] + (a.to[1] - a.from[1]) * e];
        a.pm.geometry.setCoordinates(ip);
        if (cid === selCid.current) {
          const g = L.current.routeGeom as { key: string; coords: [number, number][] } | null;
          const line = L.current.routeLine as any;
          if (g && g.coords && g.coords.length > 1 && line && line.geometry
              && typeof line.geometry.setCoordinates === "function") {
            const idx = nearestIdxFrom(g.coords, ip, L.current.routeIdx || 0);
            if (idx > 0 && idx !== L.current.routeIdx) {
              L.current.routeIdx = idx;
              line.geometry.setCoordinates(g.coords.slice(idx));
            }
          }
        }
        if (t < 1) alive = true; else anims.current.delete(cid);
      });
      rafRef.current = alive ? requestAnimationFrame(step) : 0;
    };
    rafRef.current = requestAnimationFrame(step);
  };
}
