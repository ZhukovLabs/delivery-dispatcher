"use client";

import { Check, Gauge, Package, Route as RouteIcon, Timer, X, Zap } from "lucide-react";
import type { AppState, Route } from "@/lib/api";
import { plural } from "./format";

export function RouteCard({ r, ri, st, clock, dragOverRoute, setDragOverRoute, onMoveStop, onPin, onUnassign, onGive }: {
  r: Route;
  ri: number;
  st: AppState;
  clock: (m: number) => string;
  dragOverRoute: string | null;
  setDragOverRoute: (v: string | null) => void;
  onMoveStop: (oid: string, to: string) => Promise<void>;
  onPin: (oid: string, cid: string) => Promise<void>;
  onUnassign: (oid: string) => Promise<void>;
  onGive: (r: Route) => void;
}) {
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
        e.stopPropagation(); // дальше секции плана — drop «наружу» не должен срабатывать
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
                    e.dataTransfer.setData("application/x-dp-stop", s.order_id + "|" + r.courier_id);
                    e.dataTransfer.effectAllowed = "move";
                  }}
                >
                  <span className="s-n" style={{ background: r.color }}>{stopNo}</span>
                  <span className="s-a">
                    {!!s.prio && (
                      <span className="s-prio" title={`Приоритетный${s.auto ? ", поднялся сам по возрасту" : ""}`}><Zap size={11} /></span>
                    )} {s.address}
                    {s.deadline && <span className="s-dl" title="Обещанное время доставки"><Timer size={11} />{s.deadline}</span>}
                    {!!s.late_min && s.late_min > 0 && (
                      <span className="late-chip" title="Успеть к обещанному времени не получится">
                        опоздание ~{s.late_min} мин
                      </span>
                    )}
                  </span>
                  <span className="s-t">{s.eta_clock || "?"}</span>
                  <button className="s-x" title="Убрать заказ с маршрута — вернётся в очередь готовых (сам заказ не удаляется)"
                    onClick={e => { e.stopPropagation(); void onUnassign(s.order_id); }}>
                    <X size={11} />
                  </button>
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
}
