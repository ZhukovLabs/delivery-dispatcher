"use client";

import { Bike, Undo2, X } from "lucide-react";
import type { Order } from "@/lib/api";

type Mutate = (method: string, path: string, body?: Record<string, unknown>) => Promise<void>;
type ShowToast = (msg: string, err?: boolean, act?: { label: string; fn: () => void }) => void;

export function OutOrderRows({ orders, courName, setHoverOid, focusMap, mutate, undoToast, pushUndo, showToast, doUndo }: {
  orders: Order[];
  courName: (id: string) => string;
  setHoverOid: (oid: string | null) => void;
  focusMap: (kind: "order" | "courier" | "point", id: string) => void;
  mutate: Mutate;
  undoToast: (msg: string, label: string, type: string, data: Record<string, unknown>) => void;
  pushUndo: (label: string, type: string, data: Record<string, unknown>) => void;
  showToast: ShowToast;
  doUndo: () => Promise<void>;
}) {
  return <>
    {orders.map(o => (
      <div key={o.id} className="ent ocard out" data-oid={o.id} title={o.address}
        onMouseEnter={() => setHoverOid(o.id)}
        onClick={e => {
          if ((e.target as HTMLElement).closest("button,select,input,a")) return;
          focusMap("order", o.id);
        }}>
        <span className="e-num out-ico"><Bike size={14} /></span>
        <span className="e-name">{o.address} <span className="out-chip">{courName(o.assigned || "")}</span></span>
        <span className="e-acts">
          <button className="ok" title="Вернуть в очередь готовых (не доехал, передумали)" aria-label="Вернуть в очередь"
            onClick={async () => {
              const was = o.assigned;
              await mutate("POST", `/api/orders/${o.id}/return`);
              undoToast("Заказ снова в очереди", `возврат ${o.address || ""}`.slice(0, 60), "return",
                { order_ids: [o.id], courier_id: was });
            }}><Undo2 size={14} /></button>
          <button className="no" title="Отменить (в историю)" aria-label="Отменить заказ"
            onClick={async () => {
              await mutate("DELETE", "/api/orders/" + o.id, { outcome: "cancelled" });
              pushUndo(`удаление ${o.address || ""}`.slice(0, 60), "delOrder",
                { address: o.address, lat: o.lat, lng: o.lng, prio: o.prio, deadline: o.deadline, point_id: o.point_id });
              showToast("Заказ отменён", false, { label: "Отменить", fn: () => void doUndo() });
            }}><X size={14} /></button>
        </span>
      </div>
    ))}
  </>;
}
