"use client";

import { fetchApi } from "@/lib/api";
import type { MapCtx } from "./mapLayers";
import { trimAfter } from "./mapUtils";

/* 4) линии планов: пересобираем при смене состава ИЛИ пересчёте (solved_at),
   а для выбранного курьера — ещё и при изменении его выданной части */
export function syncPlanLines(ctx: MapCtx) {
  const { ym, map, plan, pickPts, L, selCid, selCourier, roadsTick, bumpRoads, refreshSelRoute } = ctx;
  const selSig = `${selCid.current || "-"}:${selCourier?.out_route?.stops?.length || 0}:${selCourier?.out_route?.geom?.length || 0}` +
    // позиция выбранного курьера в сигнатуре: гео-тик двигает его — линия
    // маршрута перерисовывается и обрезается по новой позиции (пройденное
    // исчезает); toFixed(4) ≈ 11 м — стоящего на месте не дёргает
    (selCourier?.pos ? `@${selCourier.pos.lat.toFixed(4)},${selCourier.pos.lng.toFixed(4)}` : "");
  const lineSig = roadsTick + "|" + selSig + "|" + (plan
    ? `${plan.solved_at}|` + plan.routes.map(r => [r.courier_id, r.color, (r.trips || [])
        .map(t => t.stops.map(s => s.order_id).join(",")).join(";")].join("|")).join("~")
    : "");
  if (lineSig !== L.current.routeSig) {
    L.current.routeSig = lineSig;
    L.current.lines.forEach(l => map.geoObjects.remove(l));
    L.current.lines = [];
    if (plan && pickPts.length) {
      plan.routes.forEach(r => (r.trips || []).forEach(tr => {
        const hp = r.home_point || pickPts[0];
        const depotPt: [number, number] = [hp.lat, hp.lng];
        // дороги — норма: кэш по составу остановок; если геометрии в
        // плане нет (роутеры молчали при расчёте) — тянем /api/route
        // тем же каскадом и подменяем линию, прямыми — только резерв
        const oids = (tr.stops || []).map(s => s.order_id).join(",");
        const key = "plan|" + oids;
        const cached = (L.current.planGeom as Map<string, [number, number][]>).get(key);
        const base = cached || (tr.geometry && tr.geometry.length > 1 ? tr.geometry : null);
        const stops = (tr.stops || []).map(s => [s.lat, s.lng] as [number, number]);
        const pts = base ? trimAfter(base, stops[stops.length - 1] || depotPt)
          : [depotPt, ...stops];
        L.current.lines.push(new ym.Polyline(pts, {},
          { strokeColor: r.color, strokeWidth: 4, strokeOpacity: 0.9, zIndex: 10 }));
        if (!cached) {
          const qs = encodeURIComponent(
            [depotPt, ...stops].map(p => `${p[0].toFixed(6)},${p[1].toFixed(6)}`).join(";"));
          fetchApi(`/api/route?coords=${qs}`)
            .then(res => (res.ok ? res.json() : Promise.reject(new Error("route api"))))
            .then((j: { geometry?: [number, number][] }) => {
              if (!j.geometry || j.geometry.length < 2) return;
              (L.current.planGeom as Map<string, [number, number][]>).set(key, j.geometry);
              bumpRoads();   // перерисовать слой с дорогами
            })
            .catch(() => { /* остались как есть — прямой резерв уже нарисован */ });
        }
      }));
      L.current.lines.forEach(l => map.geoObjects.add(l));
    }
    refreshSelRoute();
  }
}
