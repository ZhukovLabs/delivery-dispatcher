"use client";

import type { AppState, Plan, Route } from "@/lib/api";
import PlanPanel from "../PlanPanel";

export default function PlanColumn({
  st, plan, busyMode, applyAdvice, giveRoute,
  dragOverRoute, setDragOverRoute, moveStop, pinOrder, unassignStop, courierToPlan, dropOutDrop,
}: {
  st: AppState;
  plan: Plan | null;
  busyMode: boolean;
  applyAdvice: (m: string) => Promise<void>;
  giveRoute: (r: Route) => Promise<void>;
  dragOverRoute: string | null;
  setDragOverRoute: (v: string | null) => void;
  moveStop: (oid: string, to: string) => Promise<void>;
  pinOrder: (oid: string, cid: string) => Promise<void>;
  unassignStop: (oid: string) => Promise<void>;
  courierToPlan: (cid: string, routeEl: Element | null) => Promise<void>;
  dropOutDrop: (e: React.DragEvent) => void;
}) {
  return (
    <section className="col-plan" id="planPanel"
      onDragOver={e => {
        if ([...e.dataTransfer.types].includes("text/plain")) {
          e.preventDefault(); e.dataTransfer.dropEffect = "move";
        }
      }}
      onDrop={async e => {
         const raw = e.dataTransfer.getData("text/plain") || "";
         if (raw.startsWith("courier:")) {
           e.preventDefault();
           await courierToPlan(raw.slice(8), (e.target as HTMLElement).closest(".route"));
           return;
         }
         await dropOutDrop(e); // стоп, брошенный между маршрутами = «наружу»
       }}>
      {!plan || !plan.routes || !plan.routes.length ? (
        <div className="plan-empty">
          {plan && plan.routes && !plan.routes.length
            ? "Решение не найдено"
            : <>Здесь появится план развозки:<br />заказы по курьерам, порядок объезда и время.</>}
        </div>
      ) : (
        <PlanPanel
          st={st} clock={clockOf(plan)} busyMode={busyMode}
          onMode={m => void applyAdvice(m)}
          onGive={r => void giveRoute(r)}
          dragOverRoute={dragOverRoute}
          setDragOverRoute={setDragOverRoute}
          onMoveStop={moveStop}
          onPin={pinOrder}
          onUnassign={unassignStop}
        />
      )}
    </section>
  );
}

/* якорь всех минут плана — момент последнего ретайма (anchored_at), не расчёта */
function clockOf(plan: Plan): (m: number) => string {
  const anchor = plan.anchored_at || plan.solved_at;
  const base = new Date(anchor || Date.now()).getTime();
  return (min: number) => new Date(base + min * 60000)
    .toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}
