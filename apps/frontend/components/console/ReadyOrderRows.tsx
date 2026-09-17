"use client";

import { Check, Copy, Flame, Hourglass, Loader2, Timer, UserX, X, Zap } from "lucide-react";
import type { AppState, Order } from "@/lib/api";
import DlPop from "./DlPop";
import { dlRound, orderAgeMin } from "./format";

type Mutate = (method: string, path: string, body?: Record<string, unknown>) => Promise<void>;
type ShowToast = (msg: string, err?: boolean, act?: { label: string; fn: () => void }) => void;

export function ReadyOrderRows({ st, tick, orders, dupOids, autoP, nowMin, planLate, planExists, inPlan, cardHl, pinning, dlEdit, setDlEdit, setHoverOid, focusMap, mutate, showToast, undoToast, pushUndo, doUndo }: {
  st: AppState;
  tick: number;
  orders: Order[];
  dupOids: Set<string>;
  autoP: number;
  nowMin: number;
  planLate: (o: Order) => number;
  planExists: boolean;
  inPlan: (o: Order) => boolean;
  cardHl: string | null;
  pinning: string | null;
  dlEdit: string | null;
  setDlEdit: (v: string | null) => void;
  setHoverOid: (oid: string | null) => void;
  focusMap: (kind: "order" | "courier" | "point", id: string) => void;
  mutate: Mutate;
  showToast: ShowToast;
  undoToast: (msg: string, label: string, type: string, data: Record<string, unknown>) => void;
  pushUndo: (label: string, type: string, data: Record<string, unknown>) => void;
  doUndo: () => Promise<void>;
}) {
  return <>
    {orders.map((o, i) => {
      void tick;
      const age = orderAgeMin(o);
      const auto = autoP > 0 && age >= autoP && !o.prio;
      const late = planLate(o);
      const nocour = planExists && !inPlan(o);
      const soon = !late && o.deadline
        ? (() => { const [h, m] = o.deadline.split(":").map(Number); return h * 60 + m - nowMin; })()
        : null;
      return (
        <div
          key={o.id}
          className={`ent ocard${o.prio ? " prio" : ""}${late > 0 ? " burning" : ""}${nocour ? " nocour" : ""}${cardHl === o.id ? " hl" : ""}${pinning === o.id ? " adding" : ""}${dupOids.has(o.id) ? " dup" : ""}`}
          data-oid={o.id}
          draggable={pinning !== o.id}
          title={`${o.address} · перетащите на курьера, чтобы выдать сразу`}
          onDragStart={e => {
            e.dataTransfer.setData("text/plain", "assign:" + o.id);
            e.dataTransfer.effectAllowed = "move";
          }}
          onMouseEnter={() => setHoverOid(o.id)}
          onClick={e => {
            if ((e.target as HTMLElement).closest("button,select,input,a")) return;
            focusMap("order", o.id);
          }}
        >
          <span className="e-num">{pinning === o.id ? <Loader2 size={13} className="spin" /> : i + 1}</span>
          <span className="e-name">
            {o.address}
            {pinning === o.id && <span className="pin-chip">пересчитываем маршрут…</span>}
            {(st.points?.length || 0) > 1 && o.point_id && st.points?.some(p => p.id === o.point_id) && (
              <span className="opt-tag" title="Место выдачи заказа">{st.points.find(p => p.id === o.point_id)!.name}</span>
            )}
            {dupOids.has(o.id) && <span className="dup-chip" title="Такой адрес уже есть в списке — проверьте, не дубль ли"><Copy size={11} /> дубль</span>}
            {nocour && <span className="nocour-chip" title="Не вошёл в расчёт: не хватило курьеров или лимита заказов. Перетащите на курьера вручную или пересчитайте план"><UserX size={11} /> без курьера</span>}
            {o.deadline && <span className="dl-chip" title="Обещали к этому времени"><Timer size={11} /> {o.deadline}</span>}
            {late > 0
              ? <span className="burn-chip" title="По текущему плану к обещанному времени не успеваем"><Flame size={11} /> опоздание ~{late} мин</span>
              : (soon !== null && 0 <= soon && soon <= 15)
                ? <span className="soon-chip" title="Дедлайн на подходе, а заказа ещё нет в маршруте"><Hourglass size={11} /> скоро {o.deadline}</span>
                : null}
             {!!o.prio && <span className="prio-tag"><Zap size={11} /> приоритет</span>}
            {auto && <span className="age-tag" title="Долго в очереди: в плане будет как приоритетный"><Hourglass size={11} /> {age} мин</span>}
            {dlEdit === o.id && (
              <DlPop
                initial={o.deadline || dlRound(new Date(Date.now() + 30 * 60000))}
                hasDeadline={!!o.deadline}
                onSave={async val => {
                  await mutate("PATCH", "/api/orders/" + o.id, { deadline: val });
                  setDlEdit(null);
                }}
                onClose={() => setDlEdit(null)}
              />
            )}
          </span>
          <span className="e-acts">
            <button title="Обещанное время доставки (дедлайн)" aria-label="Дедлайн заказа"
              onClick={() => setDlEdit(dlEdit === o.id ? null : o.id)}><Timer size={14} /></button>
            <button className={"prio-btn" + (o.prio ? " on" : "")}
              title={o.prio ? "Снять приоритет" : "Приоритет: доставить как можно раньше"}
              aria-label="Приоритет заказа"
              onClick={() => void mutate("PATCH", "/api/orders/" + o.id, { prio: !o.prio })}><Zap size={14} /></button>
            <button className="ok" title="Выдать курьеру (из текущего плана)" aria-label="Выдать заказ"
              onClick={async () => {
                const r = (st.plan?.routes || []).find(r => (r.stops || []).some(s => s.order_id === o.id));
                if (!r) { showToast("Заказа нет в текущем плане: рассчитайте план или выдайте с маршрута", true); return; }
                await mutate("POST", "/api/orders/assign", { order_ids: [o.id], courier_id: r.courier_id });
                undoToast(`Выдан: ${r.courier_name}`, `выдача ${o.address || ""}`.slice(0, 60), "assign", { order_ids: [o.id] });
              }}><Check size={14} /></button>
            <button className="no" title="Отменить (в историю)" aria-label="Отменить заказ"
              onClick={async () => {
                await mutate("DELETE", "/api/orders/" + o.id, { outcome: "cancelled" });
                pushUndo(`удаление ${o.address || ""}`.slice(0, 60), "delOrder",
                  { address: o.address, lat: o.lat, lng: o.lng, prio: o.prio, deadline: o.deadline, point_id: o.point_id });
                showToast("Заказ отменён", false, { label: "Отменить", fn: () => void doUndo() });
              }}><X size={14} /></button>
          </span>
        </div>
      );
    })}
  </>;
}
