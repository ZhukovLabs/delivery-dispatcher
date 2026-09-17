"use client";

import { useState } from "react";
import { Bike, Plus } from "lucide-react";
import type { AppState, Courier } from "@/lib/api";
import { AccHead } from "./PointsPanel";
import { CourierCard } from "./CourierCard";

/* ---------- аккордеон «Курьеры»: добавление, статусы, точка, гео/скорость, TG ---------- */

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
            return (
              <CourierCard key={c.id} c={c} n={n} st={st}
                dragOverCourier={dragOverCourier} setDragOverCourier={setDragOverCourier}
                mutate={mutate} askConfirm={askConfirm} pushUndo={pushUndo}
                focusMap={focusMap} setBindFor={setBindFor} assignOrderTo={assignOrderTo} />
            );
          })}
        </div></div>
      </div>
    </div>
  );
}
