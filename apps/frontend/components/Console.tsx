"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, isNetworkError, type AppState, type Courier, type Route } from "@/lib/api";
import { joinDepot, subscribeConn, type WsConnState } from "@/lib/ws";
import { addrKey } from "./console/format";
import { optimisticFor } from "./console/optimistic";
import { useDispatchState } from "./console/useDispatchState";
import { useUndo } from "./console/useUndo";
import TopBar from "./console/TopBar";
import PointsPanel from "./console/PointsPanel";
import OrdersPanel from "./console/OrdersPanel";
import CourierList from "./console/CourierList";
import PlanPanel from "./console/PlanPanel";
import ActivityFeed from "./console/ActivityFeed";
import BindModal from "./console/BindModal";
import Sheet from "./console/Sheet";
import { AskDialog, HelpOverlay, PickBanner, SolveOverlay, Toast } from "./console/Overlays";
import type { AskState, ToastState } from "./console/format";
import { Loader2 } from "lucide-react";

const MapView = dynamic(() => import("./MapView"), {
  ssr: false,
  loading: () => <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center", color: "#6d7688" }}>карта загружается…</div>,
});

export default function Console() {
  const { st, stLoading, stateErr, setSt, refresh, tick, dark, applyTheme } = useDispatchState();

  const [toast, setToast] = useState<ToastState | null>(null);
  const toastT = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [ask, setAsk] = useState<AskState | null>(null);

  const showToast = useCallback((msg: string, err = false, act?: ToastState["act"]) => {
    setToast({ msg, err, act });
    if (toastT.current) clearTimeout(toastT.current);
    toastT.current = setTimeout(() => setToast(null), act ? 6000 : 3400);
  }, []);

  const askConfirm = useCallback((text: string, opts: { ok?: string; danger?: boolean } = {}) =>
    new Promise<boolean>(resolve => {
      setAsk({ text, ok: opts.ok || "Да", danger: !!opts.danger, resolve });
    }), []);

  /* связь с сервером: индикатор в шапке + поведение оверлея расчёта */
  const [conn, setConn] = useState<WsConnState>("connecting");
  useEffect(() => subscribeConn(setConn), []);
  const prevConn = useRef<WsConnState>("connecting");
  useEffect(() => {
    if (prevConn.current === "offline" && conn === "online") {
      // состояние уже перезапрошено (invalidateQueries в useDispatchState),
      // юзеру остаётся короткое подтверждение; расчётный оверлей, который
      // «завис» из-за обрыва, снимается актуальным состоянием с сервера
      setSolving(false); setPinning(null);
      showToast("Связь восстановлена — данные синхронизированы");
    }
    prevConn.current = conn;
  }, [conn, showToast]);

  /* блокировка на время расчёта не должна быть вечной: пропала связь —
     через минуту снимаем оверлей сами (иначе мёртвый WS навсегда «морозит»
     консоль последним состоянием solving=true) */
  const [solveHide, setSolveHide] = useState(false);
  useEffect(() => {
    if (conn !== "offline") {
      setSolveHide(false); // связь есть — блокировка честная, таймер не нужен
      return;
    }
    const t = setTimeout(() => setSolveHide(true), 60_000);
    return () => clearTimeout(t);
  }, [conn]);

  const { undoLen, lastLabel, doUndo, undoToast, pushUndo } = useUndo(refresh, showToast);

  /* Ctrl+Z — отмена последнего действия */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "z") return;
      const t = (document.activeElement || {}).tagName || "";
      if (/INPUT|TEXTAREA|SELECT/.test(t)) return;
      if (!undoLen) return;
      e.preventDefault();
      void doUndo();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [undoLen, doUndo]);

  /* ---------- карта: выбор точки, фокус, подсветки ---------- */
  const [pickTarget, setPickTarget] = useState<"point" | "order" | null>(null);
  const [fitSignal, setFitSignal] = useState(0);
  const [hoverOid, setHoverOid] = useState<string | null>(null);
  const [cardHl, setCardHl] = useState<string | null>(null);
  const [focus, setFocus] = useState<{ kind: "order" | "courier" | "point"; id: string; n: number } | null>(null);
  const focusMap = (kind: "order" | "courier" | "point", id: string) =>
    setFocus(f => ({ kind, id, n: (f?.n || 0) + 1 }));

  /* ---------- место работы администратора ---------- */
  const [workPoint, setWorkPoint] = useState(() => (typeof window !== "undefined" ? localStorage.getItem("workPoint") || "" : ""));
  const firstPid = st?.points?.[0]?.id || "";
  const wpSynced = useRef(false);
  useEffect(() => {
    if (!st?.points?.length) return;
    setWorkPoint(cur => {
      if (st.points!.some(p => p.id === cur)) return cur;
      return st.points![0].id;
    });
  }, [st?.points]);
  useEffect(() => { if (workPoint) localStorage.setItem("workPoint", workPoint); }, [workPoint]);
  // сообщаем серверу, где мы работаем: при первом входе и при смене селектора
  useEffect(() => {
    if (!workPoint || !st?.points?.length) return;
    if (!st.points!.some(p => p.id === workPoint)) return; // сохранённая точка мертва — коррекция выше подменит id
    if (wpSynced.current || wpSwitchingRef.current) return; // ручная смена уже постит сама
    wpSynced.current = true;
    void api("/api/workpoint", "POST", { point_id: workPoint })
      .catch(() => { try { localStorage.removeItem("workPoint"); } catch {} });
  }, [workPoint, st?.points]);
  const [wpSwitching, setWpSwitching] = useState(false);
  const wpSwitchingRef = useRef(false);
  const onWorkPoint = (pid: string) => {
    if (pid === workPoint || wpSwitchingRef.current) return;
    wpSwitchingRef.current = true;
    setWpSwitching(true);
    setWorkPoint(pid);
    setHoverOid(null);   // подсветка/балун старого депо больше не актуальны
    setCardHl(null);
    void (async () => {
      try {
        await api("/api/workpoint", "POST", { point_id: pid });
        joinDepot(pid); // WS: переехать в руму нового депо (сервер пушнет его состояние)
      } catch {
        wpSynced.current = false; // точка могла стать мёртвой — эффект коррекции подменит id и повторит
      }
      // заказы, план и счётчики приходят из /api/state уже для новой точки —
      // без рефетча UI показывал бы старое депо (#1/#7)
      await refresh();
      wpSwitchingRef.current = false;
      setWpSwitching(false);
      setFitSignal(s => s + 1); // пересобрать кадр карты под новое депо
    })();
  };

  /* ---------- мутации с оптимистичным патчем ---------- */
  const mutate = async (method: string, path: string, body?: Record<string, unknown>) => {
    const opt = st ? optimisticFor(method, path, body as Record<string, any> | undefined) : undefined;
    if (opt && st) setSt(opt(st));
    try { setSt(await api<AppState>(path, method, body)); }
    catch (e) { showToast((e as Error).message, true); if (opt) void refresh(); }
  };

  /* ---------- действия ---------- */
  const [solving, setSolving] = useState(false);
  const [pinning, setPinning] = useState<string | null>(null); // заказ в фазе «в маршрут…»
  const [busyMode, setBusyMode] = useState(false); // клик по сценарию совета
  const [dragOverCourier, setDragOverCourier] = useState<string | null>(null);
  const [dragOverRoute, setDragOverRoute] = useState<string | null>(null);
  const [bindFor, setBindFor] = useState<Courier | null>(null); // привязка Telegram
  const [sheetOpen, setSheetOpen] = useState(false);
  const [sheetTab, setSheetTab] = useState<"params" | "hist" | "prof" | "team">("prof");
  const [helpOpen, setHelpOpen] = useState(false);
  const [openAcc, setOpenAcc] = useState<"points" | "orders" | "couriers" | null>("orders");

  const planClockFn = useCallback(() => {
    /* якорь всех минут плана — момент последнего ретайма (anchored_at), не расчёта */
    const anchor = st?.plan?.anchored_at || st?.plan?.solved_at;
    const base = new Date(anchor || Date.now()).getTime();
    return (min: number) => new Date(base + min * 60000)
      .toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  }, [st?.plan?.anchored_at, st?.plan?.solved_at]);

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

  /* общий прогон «тяжёлых» операций: если связи нет (сокет офлайн или обрыв
   * запроса) — оверлей НЕ гасим: за прокси мёртвый бэк выглядит как HTTP 5xx,
   * а сервер мог продолжать расчёт; истина придёт через WS или 60-с авто-скрытие.
   * Живой сервер с реальной ошибкой — обычный тост и снятие флага. */
  const connRef = useRef<WsConnState>("connecting");
  useEffect(() => { connRef.current = conn; }, [conn]);
  const runSolving = async (fn: () => Promise<void>) => {
    if (solving || !st) return;
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

  const applyAdvice = async (mode: string) => {
    if (busyMode) return;
    setBusyMode(true);
    try { setSt(await api<AppState>("/api/solve", "POST", { mode })); }
    catch (e) { showToast((e as Error).message, true); }
    finally { setBusyMode(false); }
  };

  // точка, открытая на правку в аккордеоне «Места выдачи»: клик по карте обновляет её, а не создаёт новую
  const pointEditRef = useRef<string | null>(null);
  // клик по карте при открытой форме точки идёт в форму (pendingPoint), а не в API —
  // иначе «Сохранить»/«Отмена» работают шиворот-навыворот: пик уже сохранил, кнопки его перекрывают
  const pointPickRef = useRef<((ll: { lat: number; lng: number }) => void) | null>(null);
  const registerPointPick = useCallback(
    (cb: ((ll: { lat: number; lng: number }) => void) | null) => { pointPickRef.current = cb; }, []);
  // превью несохранённой точки на карте (пик в форме места выдачи)
  const [pickPreview, setPickPreview] = useState<{ lat: number; lng: number } | null>(null);

  const onMapPick = async (ll: { lat: number; lng: number }) => {
    if (!pickTarget) return;
    const target = pickTarget;
    setPickTarget(null);
    if (target === "point" && pointPickRef.current) {
      pointPickRef.current(ll);
      setPickPreview(ll);
      return;
    }
    try {
      if (target === "point") {
        showToast("Сохраняем точку…");
        const pid = pointEditRef.current;
        const s = pid && pid !== "new"
          ? await api<AppState>("/api/points/" + pid, "POST", { address: "", lat: ll.lat, lng: ll.lng })
          : await api<AppState>("/api/points", "POST", { address: "", lat: ll.lat, lng: ll.lng });
        setSt(s);
        pointEditRef.current = null;
        showToast("Точка выдачи: " + ((s.points || []).slice(-1)[0]?.address || ""));
      } else {
        showToast("Добавляем заказ…");
        const s = await api<AppState>("/api/orders", "POST", { address: "", lat: ll.lat, lng: ll.lng });
        setSt(s);
        showToast("Новый заказ: " + s.orders[s.orders.length - 1].address);
      }
    } catch (e) { showToast((e as Error).message, true); }
  };

  const onMarkerClick = (oid: string) => {
    const card = document.querySelector(`.ocard[data-oid="${oid}"]`);
    if (card) {
      card.scrollIntoView({ block: "nearest", behavior: "smooth" });
      setCardHl(oid);
      setTimeout(() => setCardHl(null), 1600);
    }
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

  const copyRoute = async (r: Route) => {
    const clock = planClockFn();
    const lines = [`🛵 Маршрут: ${r.courier_name} (${r.count} заказ.)`];
    let k = 0;
    r.trips.forEach((tr, ti) => {
      if (r.trips.length > 1) lines.push(`Заезд ${ti + 1} (старт ≈${tr.start_clock || clock(tr.start_delay_min)})`);
      tr.stops.forEach(s => { k += 1; lines.push(`${k}. ${s.address} · ≈${s.eta_clock || clock(s.eta_min)}`); });
    });
    lines.push(`Возврат на базу ≈${clock(r.total_min)}`);
    try { await navigator.clipboard.writeText(lines.join("\n")); showToast("Маршрут скопирован, можно отправлять курьеру"); }
    catch (err) { showToast("Не удалось скопировать: " + (err as Error).message, true); }
  };

  const sendTg = async (cid: string) => {
    try {
      const r = await api<{ ok?: boolean }>("/api/notify/courier/" + cid, "POST");
      showToast(r.ok ? "Маршрут отправлен курьеру в Telegram" : "Не отправлено");
    } catch (e) { showToast((e as Error).message, true); }
  };

  /* ---------- производные ---------- */
  // дубли адресов: два диспетчера могут добавить один адрес одновременно (#3)
  // (хук обязан стоять до раннего return при !st — Rules of Hooks)
  const dupOids = useMemo(() => {
    const n = new Map<string, number>();
    (st?.orders ?? []).forEach(o => { const k = addrKey(o.address); n.set(k, (n.get(k) || 0) + 1); });
    return new Set((st?.orders ?? []).filter(o => (n.get(addrKey(o.address)) || 0) > 1).map(o => o.id));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [st?.orders]);

  if (!st) {
    const err = stateErr as Error | null;
    return <div style={{ display: "flex", height: "100vh", alignItems: "center", justifyContent: "center", color: "#6d7688" }}>
      {err ? "Ошибка загрузки: " + err.message : stLoading ? "Загрузка…" : "Нет данных"}
    </div>;
  }

  const plan = st.plan;
  const clock = planClockFn();
  const active = st.couriers.filter(c => c.status !== "off").length;
  const miss = !(st.points || []).length ? "укажите место выдачи заказов"
    : !active ? "добавьте курьера («На базе» или «В пути»)"
    : !st.orders.length ? "добавьте готовые заказы" : "";

  return (
    <>
      <TopBar
        st={st} workPoint={workPoint} firstPid={firstPid} onWorkPoint={onWorkPoint}
        undoLen={undoLen} lastLabel={lastLabel} onUndo={() => void doUndo()}
        dark={dark} onTheme={applyTheme}
        onHelp={() => setHelpOpen(true)}
        onProfile={() => { setSheetTab("prof"); setSheetOpen(true); }}
      />

      {pickTarget && <PickBanner kind={pickTarget} onCancel={() => setPickTarget(null)} />}

      <main className="console">
        <aside className="col-left">
          <PointsPanel
            st={st} open={openAcc === "points"}
            onToggle={() => setOpenAcc(a => a === "points" ? null : "points")}
            mutate={mutate} showToast={showToast} askConfirm={askConfirm}
            focusMap={focusMap} pickTarget={pickTarget} setPickTarget={setPickTarget}
            registerPointPick={registerPointPick}
            onEditChange={pid => { pointEditRef.current = pid; if (!pid) setPickPreview(null); }}
          />
          <div className="acc">
            <OrdersPanel
              st={st} tick={tick} open={openAcc === "orders"}
              onToggle={() => setOpenAcc(a => a === "orders" ? null : "orders")}
              mutate={mutate} showToast={showToast} undoToast={undoToast} pushUndo={pushUndo} doUndo={doUndo}
              focusMap={focusMap} setHoverOid={setHoverOid} cardHl={cardHl} pinning={pinning}
              dupOids={dupOids} pickTarget={pickTarget} setPickTarget={setPickTarget}
              onSolveEnter={() => void solve()}
            />
            <CourierList
              st={st} open={openAcc === "couriers"}
              onToggle={() => setOpenAcc(a => a === "couriers" ? null : "couriers")}
              mutate={mutate} showToast={showToast} askConfirm={askConfirm} pushUndo={pushUndo}
              focusMap={focusMap} setBindFor={setBindFor} assignOrderTo={assignOrderTo}
              dragOverCourier={dragOverCourier} setDragOverCourier={setDragOverCourier}
            />
          </div>

          <div className="solvebox">
            <button className="solve" disabled={!!miss || solving} onClick={() => void solve()}>
              {solving ? "⏳ Считаю…" : "⚡ Рассчитать развозку"}
            </button>
            <div className="solve-hint">{miss || ""}</div>
          </div>
        </aside>

        <section className="col-map">
          <MapView
            state={st}
            pickMode={!!pickTarget}
            onPick={ll => void onMapPick(ll)}
            fitSignal={fitSignal}
            hoverOid={hoverOid}
            onMarkerClick={onMarkerClick}
            dupOids={dupOids}
            focus={focus}
            pickPreview={pickPreview}
          />
          {wpSwitching && (
            <div className="map-loading" role="status" aria-live="polite">
              <Loader2 size={18} className="spin" /> Синхронизация места работы…
            </div>
          )}
          <button className="fit-btn" title="Показать все точки в кадре"
            style={{ position: "absolute", right: 12, bottom: 12, zIndex: 800 }}
            onClick={() => setFitSignal(s => s + 1)}>⤢</button>
          <ActivityFeed events={st?.events || []} />
        </section>

        <section className="col-plan" id="planPanel"
          onDragOver={e => {
            if ([...e.dataTransfer.types].includes("text/plain")) {
              e.preventDefault(); e.dataTransfer.dropEffect = "move";
            }
          }}
          onDrop={async e => {
            const raw = e.dataTransfer.getData("text/plain") || "";
            if (!raw.startsWith("courier:")) return;
            e.preventDefault();
            await courierToPlan(raw.slice(8), (e.target as HTMLElement).closest(".route"));
          }}>
          {!plan || !plan.routes || !plan.routes.length ? (
            <div className="plan-empty">
              {plan && plan.routes && !plan.routes.length
                ? "Решение не найдено"
                : <>Здесь появится план развозки:<br />заказы по курьерам, порядок объезда и время.</>}
            </div>
          ) : (
            <PlanPanel
              st={st} clock={clock} busyMode={busyMode}
              onMode={m => void applyAdvice(m)}
              onGive={r => void giveRoute(r)}
              onCopy={r => void copyRoute(r)}
              onTg={cid => void sendTg(cid)}
              dragOverRoute={dragOverRoute}
              setDragOverRoute={setDragOverRoute}
              onMoveStop={async (oid, to) => {
                try {
                  setSt(await api<AppState>("/api/plan/move", "POST", { order_id: oid, to_courier: to }));
                  showToast("Заказ перенесён, план обновлён");
                } catch (e) { showToast((e as Error).message, true); }
              }}
              onPin={pinOrder}
            />
          )}
        </section>
      </main>

      {sheetOpen && (
        <Sheet
          st={st} tab={sheetTab} setTab={setSheetTab} onClose={() => setSheetOpen(false)}
          setSt={setSt} showToast={showToast} askConfirm={askConfirm}
        />
      )}

      {((solving || !!st?.solving) && !solveHide) && <SolveOverlay offline={conn !== "online"} />}

      {helpOpen && <HelpOverlay onClose={() => setHelpOpen(false)} />}

      {bindFor && st && (
        <BindModal
          courier={bindFor}
          bot={st.tg?.bot || ""}
          seen={st.tg?.seen || []}
          onDone={s => { setSt(s); setBindFor(null); }}
          onClose={() => setBindFor(null)}
        />
      )}

      {ask && (
        <AskDialog ask={ask} onResolve={v => { setAsk(null); ask.resolve(v); }} />
      )}

      {toast && (
        <Toast toast={toast} onClose={() => setToast(null)} />
      )}
    </>
  );
}
