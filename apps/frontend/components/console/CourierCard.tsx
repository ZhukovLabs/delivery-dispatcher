"use client";

import { Bike, House, MapPin, Pause, X } from "lucide-react";
import type { AppState, Courier } from "@/lib/api";
import { SEG_TITLES } from "./format";
import { CourierGeoRows } from "./CourierGeoRows";

type Mutate = (method: string, path: string, body?: Record<string, unknown>) => Promise<void>;
type Confirm = (text: string, opts?: { ok?: string; danger?: boolean }) => Promise<boolean>;

const SEG_ICONS = { base: <House size={14} strokeWidth={2.2} />, away: <Bike size={14} strokeWidth={2.2} />, off: <Pause size={14} strokeWidth={2.2} /> };

export function CourierCard({ c, n, st, dragOverCourier, setDragOverCourier, mutate, askConfirm, pushUndo, focusMap, setBindFor, assignOrderTo }: {
  c: Courier;
  n: number;
  st: AppState;
  dragOverCourier: string | null;
  setDragOverCourier: (v: string | null) => void;
  mutate: Mutate;
  askConfirm: Confirm;
  pushUndo: (label: string, type: string, data: Record<string, unknown>) => void;
  focusMap: (kind: "order" | "courier" | "point", id: string) => void;
  setBindFor: (c: Courier | null) => void;
  assignOrderTo: (oid: string, cid: string) => Promise<void>;
}) {
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
      <CourierGeoRows c={c} st={st} foreign={foreign} mutate={mutate} setBindFor={setBindFor} />
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
}
