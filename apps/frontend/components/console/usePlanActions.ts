"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, isNetworkError, type AppState } from "@/lib/api";
import type { WsConnState } from "@/lib/ws";

export function usePlanActions(deps: {
  st: AppState | null;
  setSt: (s: AppState) => void;
  showToast: (msg: string, err?: boolean) => void;
  askConfirm: (text: string, opts?: { ok?: string; danger?: boolean }) => Promise<boolean>;
  conn: WsConnState;
}) {
  const { st, setSt, showToast, askConfirm, conn } = deps;
  const [solving, setSolving] = useState(false);
  const [pinning, setPinning] = useState<string | null>(null); // заказ в фазе «в маршрут…»

  /* общий прогон «тяжёлых» операций: если связи нет (сокет офлайн или обрыв
   * запроса) — оверлей НЕ гасим: за прокси мёртвый бэк выглядит как HTTP 5xx,
   * а сервер мог продолжать расчёт; истина придёт через WS или 60-с авто-скрытие.
   * Живой сервер с реальной ошибкой — обычный тост и снятие флага. */
  const connRef = useRef<WsConnState>("connecting");
  useEffect(() => { connRef.current = conn; }, [conn]);
  const runSolving = async (fn: () => Promise<void>) => {
    // блокируем и по серверному флагу: расчёт запустил другой диспетчер депо
    if (solving || !st || st.solving) return;
    setSolving(true);
    let keepOverlay = false;
    try {
      await fn();
    } catch (e) {
      if (connRef.current !== "online" || isNetworkError(e)) keepOverlay = true;
      else showToast((e as Error).message, true);
    } finally {
      if (!keepOverlay) setSolving(false);
    }
  };

  const resetSolving = useCallback(() => { setSolving(false); setPinning(null); }, []);

  const solve = () => runSolving(async () => {
    setSt(await api<AppState>("/api/solve", "POST"));
    showToast("Развозка рассчитана");
  });

  /* закрепление за курьером с видимой фазой пересчёта */
  const pinOrder = (oid: string, cid: string) => runSolving(async () => {
    setPinning(oid);
    try {
      setSt(await api<AppState>("/api/plan/pin", "POST", { order_id: oid, courier_id: cid }));
      showToast("Заказ закреплён за курьером в плане (выдать — кнопкой в маршруте)");
    } finally { setPinning(null); }
  });

  /* снять заказ с маршрута (крестик или drop наружу): вернётся в очередь готовых */
  const unassignStop = async (oid: string) => {
    if (!st || solving) return;
    try {
      setSt(await api<AppState>("/api/plan/unassign", "POST", { order_id: oid }));
      showToast("Заказ убран с маршрута — снова в очереди готовых");
    } catch (e) { showToast((e as Error).message, true); }
  };

  /* перетаскивание курьера в план: свой депо — просто в план, чужой — через подтверждение */
  const courierToPlan = async (cid: string, routeEl: Element | null) => {
    const c = st?.couriers.find(x => x.id === cid);
    if (!c || !st || solving) return;
    const pts = st.points || [];
    if (!pts.length) return;
    const firstPid = pts[0].id;
    // точка, куда бросили: маршрут под курсором или точка первых готовых заказов
    const routeCid = routeEl?.getAttribute("data-cid") || undefined;
    let targetPid: string | undefined;
    if (routeCid) {
      const rc = st.couriers.find(x => x.id === routeCid);
      targetPid = rc ? (rc.point_id || firstPid) : undefined;
    }
    if (!targetPid) {
      const ready = st.orders.filter(o => (o.status || "ready") === "ready");
      targetPid = ready[0] ? (ready[0].point_id || firstPid) : undefined;
    }
    if (!targetPid) { showToast("Нет готовых заказов — сначала добавьте заказы", true); return; }
    const ptName = (pid: string) => pts.find(p => p.id === pid)?.name || "точки";
    const hisPid = c.point_id || firstPid;
    const inPlan = (st.plan?.routes || []).some(r => r.courier_id === c.id);

    const include = () => runSolving(async () => {
      if (c.status === "off")
        await api("/api/couriers/" + c.id, "PATCH", { status: "base" });
      setSt(await api<AppState>("/api/solve", "POST", { force: [c.id] }));
      showToast(`«${c.name}» добавлен в план`);
    });

    if (hisPid === targetPid) {
      if (inPlan && c.status !== "off") { showToast(`«${c.name}» уже в плане`); return; }
      await include();
      return;
    }
    // чужая точка: разовая помощь — один заказ, без перевода курьера
    const ok = await askConfirm(
      `«${c.name}» работает на точке «${ptName(hisPid)}».\n\nПозвать его на помощь к точке «${ptName(targetPid)}»? Он возьмёт один заказ, его точка останется прежней.`,
      { ok: "Позвать на помощь" });
    if (!ok) return;
    await runSolving(async () => {
      setSt(await api<AppState>("/api/plan/help", "POST", { courier_id: c.id, point_id: targetPid }));
      showToast(`«${c.name}» приедет на помощь: возьмёт один заказ с точки «${ptName(targetPid)}»`);
    });
  };

  const moveStop = async (oid: string, to: string) => {
    try {
      setSt(await api<AppState>("/api/plan/move", "POST", { order_id: oid, to_courier: to }));
      showToast("Заказ перенесён, план обновлён");
    } catch (e) { showToast((e as Error).message, true); }
  };

  return { solving, pinning, solve, pinOrder, unassignStop, courierToPlan, moveStop, resetSolving };
}
