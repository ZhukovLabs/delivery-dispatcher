"use client";

import type { MapCtx } from "./mapLayers";
import { makeDepotLayout } from "./mapLayers";
import { esc } from "./mapUtils";

/* 1) точки выдачи: склад, всегда поверх, крупнее (#9) */
export function syncDepots(ctx: MapCtx) {
  const { ym, map, L, pickPts, pickPreview, flyTo } = ctx;
  const depotLayout = makeDepotLayout(ym);
  const seenP = new Set<string>();
  pickPts.forEach(p => {
    const key = p.id || p.name;
    seenP.add(key);
    const balloon = `<b>Место выдачи: ${esc(p.name)}</b><br>${esc(p.address)}`;
    let pm = L.current.depots.get(key);
    if (!pm) {
      pm = new ym.Placemark([p.lat, p.lng], { balloonContent: balloon },
        { iconLayout: depotLayout(), iconShape: { type: "Rectangle", coordinates: [[-21, -21], [21, 24]] },
          hideIconOnBalloonOpen: false, zIndex: 2000, cursor: "pointer" });
      const fx = () => { flyTo([p.lat, p.lng]); pm!.balloon.open(); };
      pm.events.add("click", fx);
      map.geoObjects.add(pm);
      L.current.depots.set(key, pm);
    } else {
      pm.geometry.setCoordinates([p.lat, p.lng]);
      pm.properties.set("balloonContent", balloon);
    }
  });
  for (const k of [...L.current.depots.keys()]) {
    if (!seenP.has(k)) { map.geoObjects.remove(L.current.depots.get(k)!); L.current.depots.delete(k); }
  }

  /* 1b) превью несохранённой точки: клик по карте в режиме пика */
  if (pickPreview) {
    seenP.add("__preview");
    let pv = L.current.depots.get("__preview");
    if (!pv) {
      pv = new ym.Placemark([pickPreview.lat, pickPreview.lng],
        { balloonContent: "<b>Новая точка (не сохранена)</b><br>Проверьте форму слева и нажмите «Сохранить»" },
        { preset: "islands#violetCircleDotIcon", zIndex: 1900, cursor: "help" });
      map.geoObjects.add(pv);
      L.current.depots.set("__preview", pv);
    } else {
      pv.geometry.setCoordinates([pickPreview.lat, pickPreview.lng]);
    }
  }
}
