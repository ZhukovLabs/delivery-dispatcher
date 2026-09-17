"use client";

import type { Courier } from "@/lib/api";
import type { MapCtx } from "./mapLayers";
import { swap } from "./mapLayers";
import { SCOOTER_SVG, esc } from "./mapUtils";
import { courierBalloon } from "./mapCalc";

/* 3) курьеры: меньше (#12), свои — ярко, чужие — тускло (#14), плавный ход (#11) */
export function syncCouriers(ctx: MapCtx) {
  const { ym, map, state, plan, L, selCid, anims, flyTo, bumpSel, refreshSelRoute, refreshRef, ensureRaf } = ctx;
  const seenC = new Set<string>();
  state.couriers.filter((c: Courier) => c.pos).forEach((c: Courier) => {
    seenC.add(c.id);
    const foreign = !!state.my_point && c.point_id !== state.my_point;
    const color = c.color || "#e8482b";
    const live = !!c.pos?.live;
    const sig = [c.name, color, live ? "L" : "", foreign ? "F" : "", selCid.current === c.id ? "S" : ""].join("|");
    const to: [number, number] = [c.pos!.lat, c.pos!.lng];
    let pm = L.current.couriers.get(c.id);
    if (!pm || L.current.courierSig.get(c.id) !== sig) {
      const cid = c.id;
      const html =
        `<div class="courier-marker${foreign ? " foreign" : ""}${selCid.current === c.id ? " sel" : ""}">` +
        `<div style="--c:${color}"><span class="cm-glow"></span>` +
        `${live && !foreign ? '<span class="cm-pulse"></span>' : ""}<span class="cm-body">${SCOOTER_SVG}</span>` +
        `<span class="cm-name">${esc(c.name)}</span></div></div>`;
      pm = swap(map, L.current.couriers, cid, () => new ym.Placemark(to, { balloonContent: courierBalloon(state, plan, c) },
        { iconLayout: ym.templateLayoutFactory.createClass(html),
          iconShape: { type: "Rectangle", coordinates: [[-14, -14], [14, 20]] },
          hideIconOnBalloonOpen: false, zIndex: 1000, cursor: "pointer" }));
      pm.events.add("click", () => {
        const was = selCid.current;
        selCid.current = was === cid ? null : cid;
        refreshSelRoute();
        bumpSel();
        const m = L.current.couriers.get(cid);
        const cur = m ? m.geometry.getCoordinates() : null;
        if (cur) flyTo([cur[0], cur[1]]);
        if (m && selCid.current && !m.balloon.isOpen()) m.balloon.open();
        // сигнатура с «S» изменилась — пересоберём маркер на следующем такте
        if (m) L.current.courierSig.set(cid, "");
      });
      // крестик балуна: закрыл — выбор снят, маршрут убран
      pm.balloon.events.add("userclose", () => {
        if (selCid.current === cid) {
          selCid.current = null;
          L.current.courierSig.set(cid, "");
          refreshRef.current();
          bumpSel();
        }
      });
      L.current.courierSig.set(cid, sig);
      anims.current.delete(c.id);
      return;
    }
    pm.properties.set("balloonContent", courierBalloon(state, plan, c));
    const cur = pm.geometry.getCoordinates() as [number, number];
    const jump = Math.abs(cur[0] - to[0]) > 0.02 || Math.abs(cur[1] - to[1]) > 0.02;
    if (jump) {
      pm.geometry.setCoordinates(to); // пропажа сигнала: без анимации
      anims.current.delete(c.id);
    } else if (cur[0] !== to[0] || cur[1] !== to[1]) {
      anims.current.set(c.id, { pm, from: [cur[0], cur[1]], to, start: performance.now() });
      ensureRaf();
    }
  });
  for (const k of [...L.current.couriers.keys()]) {
    if (!seenC.has(k)) {
      const pm = L.current.couriers.get(k)!;
      if (pm.balloon.isOpen()) pm.balloon.close();
      map.geoObjects.remove(pm);
      L.current.couriers.delete(k);
      L.current.courierSig.delete(k);
      anims.current.delete(k);
    }
  }
}
