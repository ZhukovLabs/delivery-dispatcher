"use client";

import { useState } from "react";
import { Bike, Gauge, House, Hourglass, Link2, MapPin, Pause, Plus, Timer, X } from "lucide-react";
import { fmtAge, type AppState, type Courier } from "@/lib/api";
import { AccHead } from "./PointsPanel";
import { SEG_TITLES } from "./format";

/* ---------- аккордеон «Курьеры»: добавление, статусы, точка, гео/скорость, TG ---------- */

const SEG_ICONS = { base: <House size={14} strokeWidth={2.2} />, away: <Bike size={14} strokeWidth={2.2} />, off: <Pause size={14} strokeWidth={2.2} /> };

type Mutate = (method: string, path: string, body?: Record<string, unknown>) => Promise<void>;
type Confirm = (text: string, opts?: { ok?: string; danger?: boolean }) => Promise<boolean>;

export default function CourierList({ st, open, onToggle, mutate, showToast, askConfirm, pushUndo, focusMap, setBindFor, assignOrderTo, dragOverCourier, setDragOverCourier }: {
  st: AppState;
  open: boolean;
  onToggle: () => void;
  mutate: Mutate;
  showToast: (msg: string, err?: boolean) => void;
  askConfirm: Confirm;
  pushUndo: (label: string, type: string, data: Record<string, unknown>) => void;
  focusMap: (kind: "order" | "courier" | "point", id: string) => void;
  setBindFor: (c: Courier | null) => void;
  assignOrderTo: (oid: string, cid: string) => Promise<void>;
  dragOverCourier: string | null;
  setDragOverCourier: (v: string | null) => void;
}) {
  const [courierName, setCourierName] = useState("");
  const add = async () => {
    const name = courierName.trim();
    if (!name) { showToast("Введите имя курьера", true); return; }
    setCourierName("");
    await mutate("POST", "/api/couriers", { name });
  };

  const carrying: Record<string, number> = {};
  st.orders.forEach(o => { if (o.status === "out" && o.assigned) carrying[o.assigned] = (carrying[o.assigned] || 0) + 1; });

  return (
    <div className={"acc-item" + (open ? " open" : "")} data-acc="couriers">
      <AccHead icon={<Bike size={15} className="acc-ico" />} label="Курьеры"
        count={st.couriers.length} open={open} onClick={onToggle} />
      <div className="acc-body"><div className="acc-inner">
        <div className="addrow">
          <input type="text" name="new_courier_name" placeholder="Имя курьера (Enter)" aria-label="Имя нового курьера"
            value={courierName} onChange={e => setCourierName(e.target.value)}
            onKeyDown={async e => { if (e.key === "Enter") await add(); }} />
          <button className="plus" title="Добавить курьера" aria-label="Добавить курьера"
            onClick={() => void add()}><Plus size={15} /></button>
        </div>
        <div id="courierList" className="ents">
          {st.couriers.length === 0 && (
            <div className="empty-state">
              <Bike size={22} />
              <b>Курьеров нет</b>
              <span>Введите имя выше — курьер появится в списке и на карте</span>
            </div>
          )}
          {st.couriers.map(c => {
            const n = carrying[c.id] || 0;
            const foreign = (st.points || []).length > 1 && c.point_id !== st.my_point;
            return (
              <div key={c.id}
                className={"ent crow" + (foreign ? " dim" : "") + (dragOverCourier === c.id ? " drop-hint" : "")}
                draggable={!foreign}
                title={foreign
                  ? `${c.name}: курьер другого депо — виден только для отслеживания`
                  : `${c.name}: перетащите в план развозки справа, чтобы включить в расчёт`}
                onClick={e => {
                  if (!c.pos) return;
                  if ((e.target as HTMLElement).closest("button,select,input,a")) return;
                  focusMap("courier", c.id);
                }}
                onDragStart={e => {
                  e.dataTransfer.setData("text/plain", "courier:" + c.id);
                  e.dataTransfer.effectAllowed = "move";
                }}
                onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; setDragOverCourier(c.id); }}
                onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragOverCourier(null); }}
                onDrop={async e => {
                  e.preventDefault();
                  setDragOverCourier(null);
                  const raw = e.dataTransfer.getData("text/plain") || "";
                  if (!raw.startsWith("assign:")) return;
                  await assignOrderTo(raw.slice(7), c.id);
                }}
              >
                <div className="c-row1">
                  <span className="cdot" style={{ background: c.color || "#94a3b8" }} title="Цвет курьера на карте и в плане" />
                  <span className="cname">{c.name}</span>
                  {(st.points || []).length > 1 && c.point_id !== st.my_point && (
                    <span className="c-depot" title="Курьер другого депо: виден для отслеживания, работает со своей точкой">
                      <MapPin size={10} />{(st.points || []).find(p => p.id === c.point_id)?.name || "—"}
                    </span>
                  )}
                  {!foreign && (
                    <span className="seg" role="group" aria-label="Статус курьера">
                      {(["base", "away", "off"] as const).map(s => (
                        <button key={s} className={c.status === s ? "on-" + s : ""}
                          title={SEG_TITLES[s]} aria-label={"Статус: " + SEG_TITLES[s]} aria-pressed={c.status === s}
                          onClick={() => { if (c.status !== s) void mutate("PATCH", "/api/couriers/" + c.id, { status: s }); }}>
                          {SEG_ICONS[s]}
                        </button>
                      ))}
                    </span>
                  )}
                  {!foreign && (
                    <span className="e-acts">
                      <button className="no" title="Удалить курьера" aria-label="Удалить курьера"
                        onClick={async () => {
                          if (!(await askConfirm(`Удалить курьера «${c.name}»?`, { ok: "Удалить", danger: true }))) return;
                          await mutate("DELETE", "/api/couriers/" + c.id);
                          pushUndo(`курьер ${c.name}`, "delCourier", { name: c.name });
                        }}><X size={14} /></button>
                    </span>
                  )}
                </div>
                {(st.points || []).length > 0 && !foreign && (
                  <div className="c-row2 pt-row" title="Место, откуда курьер забирает заказы (маршрут начинается отсюда)">
                    <MapPin size={11} className="pp-ico" />
                    <select className="pp-sel" value={c.point_id || st.points?.[0]?.id || ""}
                      aria-label="Место выдачи курьера"
                      onChange={e => {
                        const np = e.target.value;
                        if (np && np !== c.point_id)
                          void mutate("POST", `/api/couriers/${c.id}/point`, { point_id: np });
                      }}>
                      {st.points!.map(p => (
                        <option key={p.id} value={p.id}>{p.name}</option>
                      ))}
                    </select>
                  </div>
                )}
                {c.status === "away" && (c.geo
                  ? (c.geo.delivering
                       ? <div className="c-row2 geo-row" title="В развозке: заказы у курьера, возврат — по живой геолокации. Только дорога до базы: старт в плане = это время + погрузка следующей партии">
                           <Bike size={11} /> в развозке · вернётся ≈{c.geo.back_min} мин
                         </div>
                      : c.geo.has_out && c.geo.at_depot
                      ? <div className="c-row2 geo-row" title="У своей точки выдачи с заказами — фиксируем загрузку (нужен простой пару минут)">
                          <Hourglass size={11} /> у точки — выдача заказов…
                        </div>
                      : !c.geo.has_out && c.geo.at_depot
                      ? <div className="c-row2 geo-row" title="На месте, ждёт когда диспетчер отдаст заказы">
                          <House size={11} /> на точке — ждёт выдачи заказов
                        </div>
                      : c.geo.to_point_min !== undefined
                      ? <div className="c-row2 geo-row" title="Заказы ещё не отданы: сначала курьер доедет до своей точки выдачи">
                          <House size={11} /> едет за заказами · до точки ≈{c.geo.to_point_min} мин
                        </div>
                      : <div className="c-row2 geo-row" title="Возврат рассчитан по живой геолокации курьера — только дорога до базы, без погрузки. В плане старт сдвинется сильнее: это время + погрузка следующей партии">
                          <Timer size={11} /> вернётся ≈{c.geo.back_min} мин (по гео)
                        </div>)
                 : foreign
                  ? <div className="c-row2 geo-row" title="Возврат считает диспетчер его точки">
                      <Timer size={11} /> вернётся ≈{c.back_min ?? 15} мин
                    </div>
                  : <div className="c-row2 geo-row"><Timer size={11} /> вернётся через
                    <input className="backMin" type="number" name="back_min" min={0} max={480} defaultValue={c.back_min ?? 15}
                      title="Через сколько минут вернётся на базу (привяжите Telegram — будет считаться сам)" aria-label="Возврат на базу, минут"
                      onChange={e => void mutate("PATCH", "/api/couriers/" + c.id, { back_min: +e.target.value || 0 })} />
                    мин
                  </div>)}
                {c.geo?.at_order && (
                  <div className="c-row2 geo-row" title="Курьер сейчас стоит у этого заказа">
                    <Bike size={11} /> у заказа: {c.geo.at_order}
                  </div>
                )}
                {(c.cur_kmh !== undefined || c.avg_kmh !== undefined) && (
                  <div className="c-row2 spd-row">
                    {c.cur_kmh !== undefined && (
                      <span className={"spd-cur" + (c.cur_kmh > 0 ? " go" : "")}
                        title="Скорость прямо сейчас, по живой геолокации (за последние минуты)">
                        <Gauge size={11} /> {c.cur_kmh > 0 ? `${c.cur_kmh} км/ч` : "стоит"}
                      </span>
                    )}
                    <span className="spd-avg"
                      title={c.speed_src === "geo"
                        ? "Средняя скорость за сегодня — замер по геолокации, участвует в расчёте маршрутов"
                        : c.speed_src === "delivery"
                          ? "Средняя по темпу доставок за сегодня относительно других курьеров, участвует в расчёте"
                          : "Расчётная норма из настроек: замер по этому курьеру ещё не собран"}>
                      ср {(c.avg_kmh ?? 0)} км/ч{c.speed_src === "geo" ? " (гео)" : c.speed_src === "delivery" ? " (темп)" : " (норма)"}
                    </span>
                  </div>
                )}
                {st.cfg?.tg && (
                  <div className="c-row2 tg-row">
                    {c.tg_chat_id
                      ? <span className="tg-bound" title={`Telegram привязан: ${c.tg_login || c.tg_chat_id}`}
                        onClick={() => setBindFor(c)}><Link2 size={11} /> {c.tg_login || ("ID " + c.tg_chat_id)}</span>
                      : <button className="tg-btn" title="Привязать Telegram-аккаунт курьера" onClick={() => setBindFor(c)}><Link2 size={11} /> Telegram</button>}
                    {c.pos && (
                      <span className="tg-pos" title={`Геолокация обновлена${c.pos.live ? " (live-трансляция)" : ""}`}>
                        <MapPin size={11} /> {fmtAge(c.pos.ts)} назад{c.pos.live ? " · live" : ""}
                      </span>
                    )}
                  </div>
                )}
                {n > 0 && (
                  <button className="ret"
                    title={`${c.name} вернулся на базу: ${n} заказ(ов) закроются как доставленные`}
                    onClick={async () => {
                      if (!(await askConfirm(`${c.name} вернулся на базу? ${n} заказ(ов) закроются как доставленные.`, { ok: "Вернулся" }))) return;
                      await mutate("POST", `/api/couriers/${c.id}/returned`);
                    }}>🏁 Вернулся · {n} зак.</button>
                )}
              </div>
            );
          })}
        </div></div>
      </div>
    </div>
  );
}
