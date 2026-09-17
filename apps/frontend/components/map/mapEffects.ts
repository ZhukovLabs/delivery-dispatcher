"use client";

import { useEffect } from "react";
import type { MutableRefObject } from "react";
import type { AppState } from "@/lib/api";
import { fitAll } from "./mapUtils";
import type { LayerState } from "./mapLayers";

export function usePickCross(deps: {
  divRef: MutableRefObject<HTMLDivElement | null>;
  mapRef: MutableRefObject<any>;
  pickMode: boolean;
  ready: boolean;
}) {
  const { divRef, mapRef, pickMode, ready } = deps;
  useEffect(() => {
    const el = divRef.current;
    if (el) el.classList.toggle("pick-cross", !!pickMode);
    const t = setTimeout(() => mapRef.current?.container.fitToViewport(), 120);
    return () => clearTimeout(t);
  }, [pickMode, ready]);
}

/* смена депо: сбрасываем выбор и подтягиваемся к новой точке (#1/#7) */
export function useDepotChange(deps: {
  ready: boolean;
  state: AppState;
  mapRef: MutableRefObject<any>;
  lastMyPoint: MutableRefObject<string | null>;
  selCid: MutableRefObject<string | null>;
  refreshSelRoute: () => void;
  bumpSel: () => void;
  didInitialFit: MutableRefObject<boolean>;
}) {
  const { ready, state, mapRef, lastMyPoint, selCid, refreshSelRoute, bumpSel, didInitialFit } = deps;
  useEffect(() => {
    if (!ready) return;
    if (state.my_point == null) return;
    if (lastMyPoint.current === state.my_point) return;
    const first = lastMyPoint.current === null;
    lastMyPoint.current = state.my_point;
    if (first) return; // первичная загрузка — кадр сделает автофит
    selCid.current = null;
    refreshSelRoute();
    bumpSel();
    const map = mapRef.current;
    const pt = state.points?.find(p => p.id === state.my_point);
    if (map && pt) {
      map.panTo([pt.lat, pt.lng], { flying: true, delay: 0, duration: 500 });
      didInitialFit.current = false; // данные нового депо придут — пере-кадрируемся
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, state.my_point]);
}

export function useFitOnSignal(deps: {
  ymRef: MutableRefObject<any>;
  mapRef: MutableRefObject<any>;
  ready: boolean;
  fitSignal: number;
  lastSignal: MutableRefObject<number>;
  state: AppState;
}) {
  const { ymRef, mapRef, ready, fitSignal, lastSignal, state } = deps;
  useEffect(() => {
    const ym = ymRef.current, map = mapRef.current;
    if (!ready || !ym || !map) return;
    if (fitSignal !== lastSignal.current) {
      lastSignal.current = fitSignal;
      fitAll(ym, map, state);
    }
  }, [fitSignal, ready, state]);
}

/* клик по карточке в списке → летим к маркеру и открываем балун */
export function useFocusMarker(deps: {
  ready: boolean;
  focus: { kind: "order" | "courier" | "point"; id: string; n: number } | null | undefined;
  L: MutableRefObject<LayerState>;
  flyTo: (coords: [number, number]) => void;
  selCid: MutableRefObject<string | null>;
  refreshSelRoute: () => void;
  bumpSel: () => void;
}) {
  const { ready, focus, L, flyTo, selCid, refreshSelRoute, bumpSel } = deps;
  useEffect(() => {
    if (!ready || !focus || !focus.n) return;
    const pm = focus.kind === "order"
      ? L.current.orders.get(focus.id)
      : focus.kind === "courier"
        ? L.current.couriers.get(focus.id)
        : L.current.depots.get(focus.id);
    if (!pm) return;
    const g = pm.geometry.getCoordinates() as [number, number];
    flyTo(g);
    pm.balloon.open();
    if (focus.kind === "courier") {
      selCid.current = focus.id;
      refreshSelRoute();
      bumpSel();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, focus]);
}

/* ---------- ховер карточки заказа → балун на карте (без пана) ---------- */
export function useHoverBalloon(deps: {
  mapRef: MutableRefObject<any>;
  ready: boolean;
  hoverOid: string | null;
  hoverRef: MutableRefObject<string | null>;
  L: MutableRefObject<LayerState>;
}) {
  const { mapRef, ready, hoverOid, hoverRef, L } = deps;
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    if (hoverRef.current && hoverOid !== hoverRef.current) {
      const old = L.current.orders.get(hoverRef.current);
      if (old && old.balloon.isOpen()) old.balloon.close();
      hoverRef.current = null;
    }
    if (hoverOid) {
      const pm = L.current.orders.get(hoverOid);
      if (pm) {
        hoverRef.current = hoverOid;
        if (!pm.balloon.isOpen()) pm.balloon.open();
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hoverOid, ready]);
}
