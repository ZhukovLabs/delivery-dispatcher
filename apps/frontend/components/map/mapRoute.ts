"use client";

// Линия маршрута выбранного курьера: дорожная геометрия с кэшем по остановкам.
import type { MutableRefObject } from "react";
import { fetchApi, type Courier, type Plan } from "@/lib/api";
import { DEFAULT_COURIER_COLOR, trimFrom } from "./mapUtils";

export interface RouteLayerState {
  routeLine: any | null;
  routeSig: string;
  routeFetch: string;
  routeGeom: { key: string; coords: [number, number][] } | null;
  routeIdx: number;
  couriers: Map<string, any>;
}

export interface SelRouteDeps {
  ymRef: MutableRefObject<any>;
  mapRef: MutableRefObject<any>;
  L: MutableRefObject<RouteLayerState>;
  selCid: MutableRefObject<string | null>;
  couriersRef: MutableRefObject<Courier[]>;
  planRef: MutableRefObject<Plan | null>;
  pickPtsRef: MutableRefObject<{ id: string; name: string; address: string;
                                 lat: number; lng: number }[]>;
}

/* маршрут выбранного курьера: точный из плана + пунктир выданных (#5) */
export function makeRefreshSelRoute(deps: SelRouteDeps) {
  const { ymRef, mapRef, L, selCid, couriersRef, planRef, pickPtsRef } = deps;
  return function refreshSelRoute() {
    const ym = ymRef.current, map = mapRef.current;
    if (!ym || !map) return;
    // линию по возможности НЕ пересоздаём, а обновляем координаты на месте:
    // remove+add на каждом гео-тике даёт заметное мигание полилинии
    const dropLine = () => {
      if (L.current.routeLine) { map.geoObjects.remove(L.current.routeLine); }
      L.current.routeLine = null;
      L.current.routeIdx = 0;
    };
    const cid = selCid.current;
    if (!cid) { dropLine(); return; }
    const cur = couriersRef.current.find(c => c.id === cid);
    if (!cur) { dropLine(); return; }
    const r = planRef.current?.routes.find(x => x.courier_id === cid) || null;
    const orr = cur.out_route;
    if (!r && !orr) { dropLine(); return; }
    const curPts = pickPtsRef.current;
    const color = cur.color || r?.color || DEFAULT_COURIER_COLOR;
    // маршрут выбранного курьера: дороги — норма, прямые — только пока
    // ждём ответ каскада (4 ступени × 3 с) или он окончательно молчит.
    // Дорожная геометрия кэшируется по НАБОРУ ОСТАНОВОК (без позиции
    // курьера): позиция лишь обрезает линию локально — на скорости гео-тик
    // меняет координаты быстрее, чем приходит ответ, и линия «прыгала»
    // прямыми. Съезд с маршрута >250 м — маршрут изменился, кэч мимо.
    {
      let rp: [number, number][] | null = null;          // что показать, пока дорог нет
      let stopsKey: string | null = null;                // кэш-ключ: остановки
      let pos0: [number, number] | null = null;          // где курьер сейчас
      if (orr && orr.stops?.length) {
        const stops = (orr.stops || []).map(sp => [sp[0], sp[1]] as [number, number]);
        const hp = orr.home || r?.home_point || curPts[0];
        pos0 = cur.pos ? [cur.pos.lat, cur.pos.lng] as [number, number]
          : (hp ? [hp.lat, hp.lng] as [number, number] : null);
        rp = pos0 ? [pos0, ...stops] : (stops.length > 1 ? stops : null);
        stopsKey = cid + "|out|" + stops.map(p => `${p[0].toFixed(4)},${p[1].toFixed(4)}`).join(";");
      } else if (r && curPts.length) {
        const hp = r.home_point || curPts[0];
        const stops = (r.stops || []).map(s => [s.lat, s.lng] as [number, number]);
        pos0 = [hp.lat, hp.lng];
        rp = [pos0, ...stops];
        stopsKey = cid + "|plan|" + stops.map(p => `${p[0].toFixed(4)},${p[1].toFixed(4)}`).join(";");
      }
      if (rp && rp.length > 1 && stopsKey) {
        const roadStyle = { strokeColor: color, strokeWidth: 5, strokeOpacity: .95, zIndex: 30 } as const;
        // старт линии — там, где маркер ПРЯМО СЕЙЧАС (он скользит к геопозиции
        // до 2.4 с): режем по анимированной позиции, а не по цели — иначе нос
        // линии убегает вперёд курьера на время скольжения
        const mpm = (L.current.couriers as Map<string, any>).get(cid);
        const animC = mpm && mpm.geometry && mpm.geometry.getCoordinates
          ? mpm.geometry.getCoordinates() : null;
        const posDraw: [number, number] | null = animC
          ? [animC[0], animC[1]] : pos0;
        const drawRoad = (coords: [number, number][]) => {
          const line = posDraw ? trimFrom(coords, posDraw) : coords;
          if (line.length < 2) return false;
          // обновляем на месте только линию, СЕЙЧАС висящую на карте: после
          // dropLine (смена курьера) прежний объект уже снят — рисуем новую
          const live = L.current.routeLine as any;
          if (live && live.geometry
              && typeof live.geometry.setCoordinates === "function") {
            live.geometry.setCoordinates(line);          // та же дорога — на месте
          } else {
            if (live) map.geoObjects.remove(live);
            const road = new ym.Polyline(line, {}, roadStyle);
            L.current.routeLine = road;
            map.geoObjects.add(road);
          }
          L.current.routeIdx = 0;
          return true;
        };
        const cached = L.current.routeGeom;
        const ok = cached && cached.key === stopsKey && drawRoad(cached.coords);
        if (ok) return;
        // прямых у выбранного курьера не рисуем — только дороги; пока идёт
        // запрос каскада, линии нет вообще (старую чужого набора убрали)
        dropLine();
        if (L.current.routeFetch !== stopsKey) {
          L.current.routeFetch = stopsKey;
          const qs = encodeURIComponent(
            rp.map(p => `${p[0].toFixed(6)},${p[1].toFixed(6)}`).join(";"));
          fetchApi(`/api/route?coords=${qs}`)
            .then(res => (res.ok ? res.json() : Promise.reject(new Error("route api"))))
            .then((j: { geometry?: [number, number][] }) => {
              if (L.current.routeFetch !== stopsKey || selCid.current !== cid) return;
              const g = j.geometry;
              if (!g || g.length < 2) return;
              L.current.routeGeom = { key: stopsKey, coords: g };
              drawRoad(g);   // сам сменит прямой резерв на дорогу без мигания
            })
            .catch(() => {
              // каскад молчит: линий не будет до успеха, повторим на тике
              if (L.current.routeFetch === stopsKey) L.current.routeFetch = "";
            });
        }
        return;
      }
    }
    // набор остановок не собрался (нет ни выданных, ни плана) — нечего рисовать
    dropLine();
  };
}
