"use client";

import { Check, ClipboardCopy, Clock, Gauge, Hand, Package, RefreshCw, Route as RouteIcon, Send, Timer, TriangleAlert, Zap } from "lucide-react";
import type { AppState, Route } from "@/lib/api";
import AdviceCard from "./AdviceCard";
import { plural } from "./format";

/* ---------- панель плана развозки: маршруты по курьерам, заезды, стопы ---------- */

export default function PlanPanel({ st, clock, busyMode, onMode, onGive, onCopy, onTg, dragOverRoute, setDragOverRoute, onMoveStop, onPin }: {
  st: AppState;
  clock: (m: number) => string;
  busyMode: boolean;
  onMode: (m: string) => void;
  onGive: (r: Route) => void;
  onCopy: (r: Route) => void;
  onTg: (cid: string) => void;
  dragOverRoute: string | null;
  setDragOverRoute: (v: string | null) => void;
  onMoveStop: (oid: string, to: string) => Promise<void>;
  onPin: (oid: string, cid: string) => Promise<void>;
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

      {plan.routes.map((r, ri) => {
        const giveIds = r.stops.map(s => s.order_id).filter(id => {
          const o = st.orders.find(x => x.id === id);
          return o && (o.status || "ready") === "ready";
        });
        let stopNo = 0;
        return (
          <div key={r.courier_id}
            data-cid={r.courier_id}
            className={`route${ri === 0 && r.status === "base" ? " lead" : ""}${dragOverRoute === r.courier_id ? " dragover" : ""}`}
            style={{ borderLeftColor: r.color }}
            onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; setDragOverRoute(r.courier_id); }}
            onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragOverRoute(null); }}
            onDrop={async e => {
              e.preventDefault();
              setDragOverRoute(null);
              const raw = e.dataTransfer.getData("text/plain") || "";
              if (!raw || raw.startsWith("courier:")) return; // курьера обработает секция плана
              if (raw.startsWith("assign:")) { await onPin(raw.slice(7), r.courier_id); return; }
              const [oid, from] = raw.split("|");
              if (!oid || r.courier_id === from) return;
              await onMoveStop(oid, r.courier_id);
            }}
          >
            <div className="r-head">
              <span className="r-dot" style={{ background: r.color }} />
              <b>{r.courier_name}</b>
              <span className="r-acts">
                {st.cfg?.tg && r.tg_chat_id && (
                  <button className="r-tg" title="Отправить маршрут курьеру в Telegram" onClick={() => onTg(r.courier_id)}><Send size={14} /></button>
                )}
                <button className="r-copy" title="Скопировать маршрут текстом, чтобы отправить курьеру" onClick={() => onCopy(r)}><ClipboardCopy size={14} /></button>
              </span>
              {giveIds.length > 0 && (
                <button className="r-give" onClick={() => onGive(r)}
                  title={`Отметить выданным: ${giveIds.length} ${plural(giveIds.length, ["заказ уйдёт", "заказа уйдут", "заказов уйдут"])} в развозку, остальные маршруты останутся как есть`}>
                  <Check size={13} /> Выдать ({giveIds.length})
                </button>
              )}
            </div>
            <div className="r-bar">
              {r.status === "base"
                ? <span className="chip chip-green">отдать сейчас</span>
                : <span className="chip chip-amber">следующим заездом</span>}
              {r.start_delay_min > 0 && (
                <span className="chip chip-amber" title="Время до выезда со базы: для курьера в пути — возврат на базу + погрузка следующей партии. «Вернётся ≈N мин» в карточке курьера показывает только дорогу до базы, без погрузки">
                  старт +{Math.ceil(r.start_delay_min)} мин
                </span>
              )}
            </div>
            <div className="r-sub">
              <span title="Количество заказов в маршруте"><Package size={11} /> {r.count} {plural(r.count, ["заказ", "заказа", "заказов"])}</span>
              <span title="Ориентировочное время возврата на точку выдачи"><Timer size={11} /> вернётся ≈{clock(r.total_min)}</span>
              {r.distance_km ? <span title="Длина маршрута по дорогам"><RouteIcon size={11} /> {r.distance_km} км</span> : null}
              {r.speed_src && (
                <span title={r.speed_src === "geo"
                  ? "Замер по живой геолокации курьера — ETA пересчитаны под его скорость"
                  : r.speed_src === "delivery"
                    ? "Оценка по темпу доставок относительно других курьеров — ETA пересчитаны под его скорость"
                    : "Скорость курьера не замерена — ETA посчитаны по норме из параметров расчёта"}>
                  <Gauge size={11} /> ≈{r.speed_kmh} км/ч{r.speed_src === "geo" ? " (гео)" : r.speed_src === "delivery" ? " (темп)" : " (норма)"}
                </span>
              )}
            </div>
            {r.trips.map((tr, ti) => (
              <div key={ti}>
                {r.trips.length > 1 && (
                  <div className="trip-head">
                    Заезд {ti + 1} · старт ≈{tr.start_clock || clock(tr.start_delay_min)}{tr.start_delay_min ? ` (+${Math.ceil(tr.start_delay_min)} мин)` : ""}
                  </div>
                )}
                <ol className="stops">
                  {tr.stops.map(s => {
                    stopNo += 1;
                    return (
                      <li key={s.order_id} draggable
                        title={`${s.address} · +${s.eta_min} мин от расчёта`}
                        onDragStart={e => {
                          e.dataTransfer.setData("text/plain", s.order_id + "|" + r.courier_id);
                          e.dataTransfer.effectAllowed = "move";
                        }}
                      >
                        <span className="s-n" style={{ background: r.color }}>{stopNo}</span>
                        <span className="s-a">
                          {s.prio && (
                            <span className="s-prio" title={`Приоритетный${s.auto ? ", поднялся сам по возрасту" : ""}`}><Zap size={11} /></span>
                          )} {s.address} {s.deadline && <span className="s-dl" title="Обещанное время доставки"><Timer size={11} />{s.deadline}</span>}
                          {!!s.late_min && s.late_min > 0 && (
                            <span className="late-chip" title="Успеть к обещанному времени не получится">
                              опоздание ~{s.late_min} мин
                            </span>
                          )}
                        </span>
                        <span className="s-t">{s.eta_clock || "?"}</span>
                      </li>
                    );
                  })}
                </ol>
                {r.trips.length > 1 && (
                  <div className="trip-head" style={{ margin: "1px 0 0", fontWeight: 400 }}>
                    вернётся на базу ≈{tr.end_clock || clock(tr.total_min)} · {tr.distance_km ? tr.distance_km + " км" : ""}
                  </div>
                )}
              </div>
            ))}
          </div>
        );
      })}
    </>
  );
}
