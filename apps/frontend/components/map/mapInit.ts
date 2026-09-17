"use client";

import { useEffect } from "react";
import type { MutableRefObject } from "react";
import { loadYmaps } from "./loadYmaps";
import type { LayerState } from "./mapLayers";

/* ---------- инициализация карты ---------- */
export function useInitMap(deps: {
  divRef: MutableRefObject<HTMLDivElement | null>;
  mapRef: MutableRefObject<any>;
  ymRef: MutableRefObject<any>;
  pickRef: MutableRefObject<boolean>;
  onPickRef: MutableRefObject<(ll: { lat: number; lng: number }) => void>;
  selCid: MutableRefObject<string | null>;
  refreshRef: MutableRefObject<() => void>;
  L: MutableRefObject<LayerState>;
  rafRef: MutableRefObject<number>;
  setReady: (v: boolean) => void;
  setErr: (v: string | null) => void;
  bumpSel: () => void;
}) {
  const { divRef, mapRef, ymRef, pickRef, onPickRef, selCid, refreshRef, L, rafRef, setReady, setErr, bumpSel } = deps;
  useEffect(() => {
    let dead = false;
    loadYmaps().then(ym => {
      if (dead || !divRef.current || mapRef.current) return;
      ymRef.current = ym;
      const map = new ym.Map(divRef.current, {
        center: [52.4345, 31.0137],
        zoom: 13,
        controls: ["zoomControl", "fullscreenControl", "geolocationControl"],
      }, { suppressMapOpenBlock: true, yandexMapDisablePoiInteractivity: true });
      mapRef.current = map;
      // ховер-балуны не должны дёргать карту — подтягиваемся только по клику (#8)
      map.options.set("balloonAutoPan", false);
      map.events.add("click", (e: any) => {
        if (pickRef.current) {
          const c = e.get("coords") as [number, number];
          onPickRef.current({ lat: c[0], lng: c[1] });
          return;
        }
        // клик мимо маркеров — снять выбор курьера, убрать маршрут и радиус
        if (selCid.current) {
          selCid.current = null;
          refreshRef.current();
          bumpSel();
        }
        if (L.current.orderCircle) {
          map.geoObjects.remove(L.current.orderCircle);
          L.current.orderCircle = null;
          L.current.orderCircleOid = null;
        }
      });
      map.container.fitToViewport();
      setReady(true);
    }).catch(() => !dead && setErr("Карта не загрузилась — проверьте интернет"));
    return () => {
      dead = true;
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      if (mapRef.current) { mapRef.current.destroy(); mapRef.current = null; }
    };
  }, []);
}
