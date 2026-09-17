// Расчёт содержимого балуна и ETA: чистые функции от state/плана.
import { fmtAge, type Plan, type Courier, type Order, type AppState } from "@/lib/api";
import { esc, havKm, routeOf } from "./mapUtils";

export interface PopupData {
  address: string; courier?: string; eta?: string;
  inMin?: number; kmLeft?: number; lateMin?: number;
  prio?: boolean; deadline?: string; note?: string;
  onSite?: boolean;
}

/* содержимое балуна заказа: адрес/заказ → курьер → время прибытия →
   приоритет → дедлайн → опоздание (см. требование к точке доставки) */
export const popupHtml = (p: PopupData) =>
  `<b>${esc(p.address)}</b>` +
  (p.courier ? `<br>Курьер: ${esc(p.courier)}` : "") +
  (p.onSite ? "<br>📍 курьер на месте" : "") +
  (p.eta ? `<br>Время прибытия ≈${esc(p.eta)}` +
    (p.inMin != null ? ` (через ${p.inMin} мин)` : "") : "") +
  (p.kmLeft != null ? `<br>осталось ${p.kmLeft.toFixed(2)} км` : "") +
  (p.lateMin ? `<br><span style="color:#b3261e">опоздание ~${p.lateMin} мин</span>` : "") +
  (p.prio ? "<br>⭐ приоритетный" : "") +
  (p.deadline ? `<br>⏰ до ${esc(p.deadline)}` : "") +
  (p.note ? `<br>${p.note}` : "");

// ETA выданного заказа: по остатку его маршрута из позиции курьера.
// Скорость — реальная курьера (avg_kmh уже включает трафик), не дефолт настроек
export const outEta = (state: AppState, o: Order, cour: Courier): { min: number; km: number } | null => {
  const stops = (cour.out_route?.stops || [])
    .filter(sp => sp.length > 2) as [number, number, string][];
  if (!cour.pos || !stops.length) return null;
  const idx = stops.findIndex(sp => sp[2] === o.id);
  if (idx < 0) return null;
  const pts: [number, number][] = [[cour.pos.lat, cour.pos.lng],
    ...stops.slice(0, idx + 1).map(sp => [sp[0], sp[1]] as [number, number])];
  let km = 0;
  for (let i = 0; i < pts.length - 1; i++) km += havKm(pts[i], pts[i + 1]);
  const kmh = cour.avg_kmh && cour.avg_kmh > 20 ? cour.avg_kmh
    : (state.settings.speed_kmh || 60);
  const min = km / (kmh / 60) + (state.settings.handover_min ?? 5) * idx;
  return { min, km };
};

// живой остаток выданного маршрута для балуна курьера: от текущей позиции
// через оставшиеся остановки, скорость — реальная курьера
export const outRemain = (state: AppState, c: Courier): { min: number; n: number } | null => {
  const stops = (c.out_route?.stops || []).filter(sp => sp.length > 2);
  if (!c.pos || !stops.length) return null;
  const pts: [number, number][] = [[c.pos.lat, c.pos.lng],
    ...stops.map(sp => [sp[0], sp[1]] as [number, number])];
  let km = 0;
  for (let i = 0; i < pts.length - 1; i++) km += havKm(pts[i], pts[i + 1]);
  const kmh = c.avg_kmh && c.avg_kmh > 20 ? c.avg_kmh
    : (state.settings.speed_kmh || 60);
  return { min: km / (kmh / 60) + (state.settings.handover_min ?? 5) * (stops.length - 1),
           n: stops.length };
};

/* пин заказа из плана: цвет курьера + порядковый номер + балун */
export interface PlanPin { color: string; label: string; popup: string }

export const buildPlanMap = (plan: Plan | null): Record<string, PlanPin> => {
  const planMap: Record<string, PlanPin> = {};
  if (!plan) return planMap;
  // на пине — порядковый номер доставки в маршруте ЭТОГО курьера
  // (цвет уже несёт курьера; у каждого маршрута своя очередь с 1)
  plan.routes.forEach(r => {
    let k = 0;
    r.stops.forEach(s => {
      k += 1;
      planMap[s.order_id] = {
        color: r.color,
        label: String(k),
        popup: popupHtml({ address: s.address, courier: r.courier_name,
                           eta: s.eta_clock, inMin: s.eta_min, lateMin: s.late_min,
                           prio: s.prio, deadline: s.deadline }),
      };
    });
  });
  return planMap;
};

export const courierBalloon = (state: AppState, plan: Plan | null, c: Courier) => {
  const r = routeOf(plan, c.id);
  const endClk = r && r.trips?.length ? r.trips[r.trips.length - 1].end_clock : undefined;
  const rem = outRemain(state, c);
  return `<b>${esc(c.name)}</b><br>📍 ${fmtAge(c.pos!.ts)} назад${c.pos!.live ? " · live" : ""}` +
    (c.pos!.acc ? ` · ±${Math.round(c.pos!.acc)} м` : "") +
    (rem ? `<br>⏳ осталось ≈${Math.round(rem.min)} мин · ${rem.n} зак.` :
      (r ? `<br>Маршрут: ${r.count} зак. · ≈${Math.round(r.total_min)} мин · финиш ${endClk || "?"}` : "")) +
    (!r && !rem && (c.out_route?.geom?.length || 0) > 1 ? "<br>↩ Возвращается на базу" : "");
};
