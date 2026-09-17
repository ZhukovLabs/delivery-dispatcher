"use client";

import { useMemo, useRef, useState } from "react";
import type { AppState, Order } from "@/lib/api";
import type { GeoItem } from "../GeoInput";
import { addrKey } from "./format";

type Mutate = (method: string, path: string, body?: Record<string, unknown>) => Promise<void>;
type ShowToast = (msg: string, err?: boolean, act?: { label: string; fn: () => void }) => void;

export function useOrdersState({ st, dupOids, mutate, showToast }: {
  st: AppState;
  dupOids: Set<string>;
  mutate: Mutate;
  showToast: ShowToast;
}) {
  const pendingOrder = useRef<GeoItem | null>(null);
  const [orderNote, setOrderNote] = useState("");
  const orderLabel = useRef("");
  const orderInputRef = useRef<HTMLInputElement | null>(null);
  const [dlEdit, setDlEdit] = useState<string | null>(null);

  const addOrder = async () => {
    const p = pendingOrder.current;
    if (!p) { showToast("Укажите точку: подсказкой в поле или кнопкой 📍 по карте", true); return; }
    await mutate("POST", "/api/orders", { address: orderLabel.current || p.label, lat: p.lat, lng: p.lng });
    pendingOrder.current = null;
    orderLabel.current = "";
    setOrderNote("");
    orderInputRef.current?.focus();
  };

  const autoP = +(st.settings.auto_prio_min || 0);
  const nowMin = new Date().getHours() * 60 + new Date().getMinutes();
  const planLate = (o: Order) => {
    const plan = st.plan;
    if (!plan || !plan.routes) return 0;
    for (const r of plan.routes) {
      const s = (r.stops || []).find(s => s.order_id === o.id);
      if (s) return s.late_min || 0;
    }
    return 0;
  };
  // заказ остался без курьера после расчёта: план есть, а заказа в маршрутах нет
  const planExists = !!(st.plan && (st.plan.routes?.length || 0) > 0);
  const inPlan = (o: Order) =>
    !!st.plan?.routes?.some(r => (r.stops || []).some(s => s.order_id === o.id));
  // дубли адресов: два диспетчера могут добавить один адрес одновременно (#3);
  // группа встаёт на место первого вхождения адреса
  const readyOrders = useMemo(() => {
    const readyRaw = st.orders.filter(o => (o.status || "ready") === "ready");
    const emitted = new Set<string>();
    const res: Order[] = [];
    for (const o of readyRaw) {
      const k = addrKey(o.address);
      if (!dupOids.has(o.id)) { res.push(o); continue; }
      if (emitted.has(k)) continue;
      for (const x of readyRaw) {
        if (addrKey(x.address) === k) res.push(x);
      }
      emitted.add(k);
    }
    return res;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [st.orders, dupOids]);
  const outOrders = st.orders.filter(o => o.status === "out");
  const courName = (id: string) => st.couriers.find(c => c.id === id)?.name || "";

  return {
    pendingOrder, orderNote, setOrderNote, orderLabel, orderInputRef, addOrder,
    autoP, nowMin, planLate, planExists, inPlan, readyOrders, outOrders, courName,
    dlEdit, setDlEdit,
  };
}
