"use client";

import { useState } from "react";
import { api, type AppState, type Route } from "@/lib/api";

export function useOrderActions(deps: {
  st: AppState | null;
  setSt: (s: AppState) => void;
  mutate: (method: string, path: string, body?: Record<string, unknown>) => Promise<void>;
  showToast: (msg: string, err?: boolean) => void;
  undoToast: (msg: string, label: string, type: string, data: Record<string, unknown>) => void;
}) {
  const { st, setSt, mutate, showToast, undoToast } = deps;
  const [busyMode, setBusyMode] = useState(false); // клик по сценарию совета

  const assignOrderTo = async (oid: string, cid: string) => {
    const o = st?.orders.find(x => x.id === oid);
    if (!o || o.status === "out") return;
    const c = st?.couriers.find(x => x.id === cid);
    if (!c) return;
    if (c.status === "off") { showToast(`${c.name} недоступен: включите его статусом`, true); return; }
    const oPid = o.point_id || st?.points?.[0]?.id || "";
    const cPid = c.point_id || st?.points?.[0]?.id || "";
    if ((st?.points?.length || 0) > 1 && oPid !== cPid) {
      const pn = st?.points?.find(p => p.id === oPid)?.name || "";
      showToast(`Заказ из точки «${pn}» — выдать может только курьер этой точки`, true); return;
    }
    await mutate("POST", "/api/orders/assign", { order_ids: [oid], courier_id: cid });
    undoToast(`Выдан: ${c.name}`, `выдача ${o.address || ""}`.slice(0, 60), "assign", { order_ids: [oid] });
  };

  const giveRoute = async (r: Route) => {
    const ids = r.stops.map(s => s.order_id).filter(id => {
      const o = st?.orders.find(x => x.id === id);
      return o && (o.status || "ready") === "ready";
    });
    if (!ids.length) { showToast("Все заказы маршрута уже выданы", true); return; }
    await mutate("POST", "/api/orders/assign", { order_ids: ids, courier_id: r.courier_id });
    undoToast(`✓ Выдано ${r.courier_name}: ${ids.length} зак.`, `выдача маршрута ${r.courier_name}`, "assign", { order_ids: ids });
  };

  const applyAdvice = async (mode: string) => {
    if (busyMode) return;
    setBusyMode(true);
    try { setSt(await api<AppState>("/api/solve", "POST", { mode })); }
    catch (e) { showToast((e as Error).message, true); }
    finally { setBusyMode(false); }
  };

  return { busyMode, assignOrderTo, giveRoute, applyAdvice };
}
