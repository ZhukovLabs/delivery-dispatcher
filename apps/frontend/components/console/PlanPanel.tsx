"use client";

import { Clock, Hand, RefreshCw, TriangleAlert } from "lucide-react";
import type { AppState, Route } from "@/lib/api";
import AdviceCard from "./AdviceCard";
import { RouteCard } from "./RouteCard";

/* ---------- панель плана развозки: маршруты по курьерам, заезды, стопы ---------- */

export default function PlanPanel({ st, clock, busyMode, onMode, onGive, dragOverRoute, setDragOverRoute, onMoveStop, onPin, onUnassign }: {
  st: AppState;
  clock: (m: number) => string;
  busyMode: boolean;
  onMode: (m: string) => void;
  onGive: (r: Route) => void;
  dragOverRoute: string | null;
  setDragOverRoute: (v: string | null) => void;
  onMoveStop: (oid: string, to: string) => Promise<void>;
  onPin: (oid: string, cid: string) => Promise<void>;
  onUnassign: (oid: string) => Promise<void>;
}) {
  const plan = st.plan!;
  const byRoads = plan.routing === "roads";

  return (
    <>
      <div className="plan-top">
        <div className="pt-label">Последняя доставка</div>
        <div className="pt-clock">≈{plan.last_delivery_clock || "?"} <small>+{plan.last_delivery_min} мин</small></div>
        <div className="pt-meta">
          {!byRoads && (
            <span className="pm-chip warn" title="Сервисы дорог (ORS/OSRM) недоступны — время и километры оценены по прямой, с запасом">
              <TriangleAlert size={11} /> расчёт по прямой
            </span>
          )}
          <span className="pm-chip" title="Время последнего расчёта плана">
            <Clock size={11} />
            рассчитано {(plan.solved_at || "").replace("T", " ").slice(11, 16)}
          </span>
          {plan.stale && (
            <span className="pm-chip warn" title="Данные менялись после расчёта">
              <RefreshCw size={11} /> устарел — нажмите «Рассчитать»
            </span>
          )}
          {plan.moved && (
            <span className="pm-chip" title="Порядок объезда правили перетаскиванием">
              <Hand size={11} /> правка вручную
            </span>
          )}
          {!!plan.unassigned && (
            <span className="pm-chip warn" title="Заказы, не поместившиеся ни в один маршрут (лимит заказов на курьера)">
              <TriangleAlert size={11} /> без маршрута: {plan.unassigned}
            </span>
          )}
        </div>
      </div>

      {plan.advice && <AdviceCard a={plan.advice} busy={busyMode} onMode={onMode} />}

      {plan.routes.map((r, ri) => (
        <RouteCard key={r.courier_id} r={r} ri={ri} st={st} clock={clock}
          dragOverRoute={dragOverRoute} setDragOverRoute={setDragOverRoute}
          onMoveStop={onMoveStop} onPin={onPin} onUnassign={onUnassign} onGive={onGive} />
      ))}
    </>
  );
}
