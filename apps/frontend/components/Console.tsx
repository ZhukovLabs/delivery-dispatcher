"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, fmtCoords, type AppState, type Courier, type Order, type Route, type Advice } from "@/lib/api";
import GeoInput, { type GeoItem } from "./GeoInput";
import { Bike, Check, ChevronDown, CircleHelp, Flame, History, Hourglass, House, Link2, Loader2, LogOut, MapPin, Moon, Package, PackageOpen, Pause, Pencil, Plus, Route as RouteIcon, Settings, ShieldCheck, SlidersHorizontal, Sun, Timer, Trash2, Undo2, User, Users, X, Zap } from "lucide-react";

const MapView = dynamic(() => import("./MapView"), {
  ssr: false,
  loading: () => <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center", color: "#6d7688" }}>карта загружается…</div>,
});

/* ---------- утилиты ---------- */
const SEG_ICONS = { base: <House size={14} strokeWidth={2.2} />, away: <Bike size={14} strokeWidth={2.2} />, off: <Pause size={14} strokeWidth={2.2} /> };
const SEG_TITLES: Record<string, string> = {
  base: "На базе: отдать сейчас",
  away: "В пути: следующим заездом",
  off: "Не участвует в расчёте",
};

/** Заголовок секции-аккордеона: иконка, название, счётчик, шеврон. */
function AccHead({ icon, label, count, open, onClick }: {
  icon: React.ReactNode; label: string; count: number; open: boolean; onClick: () => void;
}) {
  return (
    <button className="acc-head" onClick={onClick} aria-expanded={open}>
      {icon}{label}
      <span className={"p-count" + (count ? " on" : "")}>{count}</span>
      <ChevronDown size={15} className="chev" />
    </button>
  );
}

function declName(n: string) {
  if (!n) return "";
  if (!/[а-яё]$/i.test(n)) return n;
  if (n.endsWith("ий")) return n.slice(0, -2) + "ия";
  if (n.endsWith("я")) return n.slice(0, -1) + "и";  if (n.endsWith("а")) return n.slice(0, -1) + "ы";
  return n + "а";
}
const posAgeMin = (ts: number) => Math.max(0, Math.round((Date.now() - ts * 1000) / 60000));
const shortAddr = (s: string) =>
  s.replace(/,?\s*Гомель$/i, "").replace(/\s*сельский Совет$/i, "").replace(/(^|\s)улица\s/i, "$1").trim();

const orderAgeMin = (o: Order) => {
  try { return Math.max(0, Math.round((Date.now() - new Date(o.created_at).getTime()) / 60000)); }
  catch { return 0; }
};
const dlRound = (d: Date) => {
  const x = new Date(d);
  x.setMinutes(Math.ceil(x.getMinutes() / 5) * 5, 0, 0);
  return `${String(x.getHours()).padStart(2, "0")}:${String(x.getMinutes()).padStart(2, "0")}`;
};

interface UndoEntry { label: string; type: string; data: Record<string, unknown>; }
interface ToastState { msg: string; err?: boolean; act?: { label: string; fn: () => void }; }
interface AskState { text: string; ok: string; danger: boolean; resolve: (v: boolean) => void; }

export default function Console() {
  /* ---------- состояние через TanStack Query: кэш + синхронизация по фокусу окна.
     План считается ТОЛЬКО по кнопке «Рассчитать». Живые обновления (несколько
     админов, геолокации курьеров) — long-poll /api/rev ниже. ---------- */
  const qc = useQueryClient();
  const { data: stData, error: stateErr, isPending: stLoading } = useQuery({
    queryKey: ["state"],
    queryFn: () => api<AppState>("/api/state"),
    staleTime: 10000,
    retry: 1,
    refetchOnWindowFocus: "always",
  });

  // живые обновления: long-poll /api/rev — висит, пока состояние не изменится
  // (кто-то из админов что-то сделал / курьер прислал геолокацию), тогда
  // инвалидируем кэш и каждый браузер перезаказывает /api/state сам.
  // rev берём из уже загруженного state — иначе первый опрос с since=0
  // будил нас сразу и дёргал лишний /api/state на каждой загрузке страницы
  const revRef = useRef(0);
  useEffect(() => { if (stData?.rev != null) revRef.current = stData.rev; }, [stData]);
  useEffect(() => {
    let stop = false;
    (async () => {
      while (!stop) {
        try {
          const r = await fetch(`/api/rev?since=${revRef.current}`);
          if (r.status === 401) { location.assign("/login"); return; }
          const d = await r.json();
          if (d.rev > revRef.current) {
            revRef.current = d.rev;
            qc.invalidateQueries({ queryKey: ["state"] });
          }
        } catch { await new Promise(res => setTimeout(res, 3000)); }
      }
    })();
    return () => { stop = true; };
  }, [qc]);
  const st = stData ?? null;
  const setSt = useCallback((s: AppState) => { qc.setQueryData(["state"], s); }, [qc]);
  const refresh = useCallback(async () => { await qc.invalidateQueries({ queryKey: ["state"] }); }, [qc]);
  const [tick, setTick] = useState(0); // ежеминутное обновление возраста/бейджей
  const [dark, setDark] = useState(false);
  const [toast, setToast] = useState<ToastState | null>(null);
  const toastT = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [ask, setAsk] = useState<AskState | null>(null);
  const undoStack = useRef<UndoEntry[]>([]);
  const [undoLen, setUndoLen] = useState(0);

  const [pickTarget, setPickTarget] = useState<"point" | "order" | null>(null);
  const [fitSignal, setFitSignal] = useState(0);
  const [hoverOid, setHoverOid] = useState<string | null>(null);
  const [cardHl, setCardHl] = useState<string | null>(null);

  // редактируемая точка выдачи: null - список закрыт, "new" - добавление, id - правка
  const [pointEdit, setPointEdit] = useState<string | null>(null);
  const pendingPoint = useRef<GeoItem | null>(null);
  const [pointNote, setPointNote] = useState("");
  const [pointName, setPointName] = useState("");
  const pendingOrder = useRef<GeoItem | null>(null);
  const [orderNote, setOrderNote] = useState("");
  const orderLabel = useRef("");
  const orderInputRef = useRef<HTMLInputElement | null>(null);

  const [dlEdit, setDlEdit] = useState<string | null>(null);
  const [solving, setSolving] = useState(false);
  const [pinning, setPinning] = useState<string | null>(null); // заказ в фазе «в маршрут…»
  const [busyMode, setBusyMode] = useState(false); // клик по сценарию совета
  const [sheetOpen, setSheetOpen] = useState(false);
  const [sheetTab, setSheetTab] = useState<"params" | "hist" | "users">("params");
  const [helpOpen, setHelpOpen] = useState(false);
  const [openAcc, setOpenAcc] = useState<"points" | "orders" | "couriers" | null>("orders");
  const [histDays, setHistDays] = useState("1");
  const [hist, setHist] = useState<{ rows?: unknown[]; summary?: Record<string, number | null> } | null>(null);
  const [week, setWeek] = useState<string>("");
  const [courierName, setCourierName] = useState("");
  const [bindFor, setBindFor] = useState<Courier | null>(null); // привязка Telegram
  const [pwOld, setPwOld] = useState("");
  const [pwNew, setPwNew] = useState("");
  const [userEmail, setUserEmail] = useState("");
  const [userPwd, setUserPwd] = useState("");
  const [userIsAdmin, setUserIsAdmin] = useState(false);
  const [dragOverCourier, setDragOverCourier] = useState<string | null>(null);
  const [dragOverRoute, setDragOverRoute] = useState<string | null>(null);
  const [workPoint, setWorkPoint] = useState(() => (typeof window !== "undefined" ? localStorage.getItem("workPoint") || "" : "")); // точка выдачи, в которой работает администратор

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
    if (wpSynced.current) return;
    wpSynced.current = true;
    void api("/api/workpoint", "POST", { point_id: workPoint }).catch(() => {});
  }, [workPoint, st?.points]);
  const onWorkPoint = (pid: string) => {
    setWorkPoint(pid);
    wpSynced.current = false;
    void api("/api/workpoint", "POST", { point_id: pid }).catch(() => {});
  };
  const curPoint = st?.points?.find(p => p.id === workPoint) || st?.points?.[0] || null;

  const showToast = useCallback((msg: string, err = false, act?: ToastState["act"]) => {
    setToast({ msg, err, act });
    if (toastT.current) clearTimeout(toastT.current);
    toastT.current = setTimeout(() => setToast(null), act ? 6000 : 3400);
  }, []);

  const askConfirm = useCallback((text: string, opts: { ok?: string; danger?: boolean } = {}) =>
    new Promise<boolean>(resolve => {
      setAsk({ text, ok: opts.ok || "Да", danger: !!opts.danger, resolve });
    }), []);

  /* старт, тикер возраста, Ctrl+Z, тема, закрытие дедлайн-попапа по клику мимо */
  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
    const iv = setInterval(() => { if (!document.hidden) setTick(t => t + 1); }, 60000);
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "z") return;
      const t = (document.activeElement || {}).tagName || "";
      if (/INPUT|TEXTAREA|SELECT/.test(t)) return;
      if (!undoStack.current.length) return;
      e.preventDefault();
      void doUndo();
    };
    document.addEventListener("keydown", onKey);
    /* free-тариф облака засыпает через 15 мин тишины: пока вкладка открыта — лёгкий пинг, чтобы не ждать холодный старт */
    const hb = /^(localhost|127\.)/.test(location.hostname)
      ? null
      : setInterval(() => { if (!document.hidden) void fetch("/health", { cache: "no-store" }).catch(() => {}); }, 10 * 60 * 1000);
    return () => { clearInterval(iv); if (hb) clearInterval(hb); document.removeEventListener("keydown", onKey); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const applyTheme = (d: boolean) => {
    setDark(d);
    document.documentElement.classList.toggle("dark", d);
    localStorage.setItem("theme", d ? "dark" : "light");
  };

  /* ---------- отмена действий ---------- */
  const pushUndo = (label: string, type: string, data: Record<string, unknown>) => {
    undoStack.current.push({ label, type, data });
    if (undoStack.current.length > 20) undoStack.current.shift();
    setUndoLen(undoStack.current.length);
  };
  const performUndo = async (u: UndoEntry): Promise<void> => {
    if (u.type === "delOrder") await api("/api/orders", "POST", u.data);
    else if (u.type === "assign")
      for (const oid of u.data.order_ids as string[]) {
        try { await api(`/api/orders/${oid}/return`, "POST"); } catch { /* уже вернулся */ }
      }
    else if (u.type === "return") await api("/api/orders/assign", "POST", u.data);
    else if (u.type === "delCourier") await api("/api/couriers", "POST", u.data);
    else if (u.type === "cancel") await api("/api/orders", "POST", u.data);
  };
  const doUndo = async () => {
    const u = undoStack.current.pop();
    setUndoLen(undoStack.current.length);
    if (!u) return;
    try { await performUndo(u); await refresh(); showToast(`Отменено: ${u.label}`); }
    catch (e) { showToast((e as Error).message, true); }
  };
  const undoToast = (msg: string, label: string, type: string, data: Record<string, unknown>) => {
    pushUndo(label, type, data);
    showToast(msg, false, { label: "Отменить", fn: () => void doUndo() });
  };

  /* ---------- действия ---------- */
  /* оптимистичный патч: мгновенный отклик, серверная правда при ответе, откат при ошибке */
  const optimisticFor = (method: string, path: string, body: Record<string, any> | undefined): ((s: AppState) => AppState) | undefined => {
    const patch = (fn: (s: AppState) => void): ((s: AppState) => AppState) =>
      (s: AppState) => { const c = { ...s, orders: [...s.orders], couriers: [...s.couriers] }; fn(c); return c; };
    if (method === "POST" && path === "/api/orders" && body?.lat !== undefined)
      return patch(s => { s.orders.push({ id: "tmp-" + Date.now(), address: String(body.address || "Точка"), lat: body.lat, lng: body.lng, point_id: body.point_id ? String(body.point_id) : undefined, created_at: new Date().toISOString(), status: "ready" }); });
    if (method === "POST" && path === "/api/orders/assign" && Array.isArray(body?.order_ids))
      return patch(s => { const name = s.couriers.find(c => c.id === body.courier_id)?.name || ""; s.orders = s.orders.map(o => body.order_ids.includes(o.id) ? { ...o, status: "out" as const, assigned: name } : o); });
    if (method === "POST" && /^\/api\/orders\/[^/]+\/return$/.test(path))
      return patch(s => { s.orders = s.orders.map(o => o.id === path.split("/")[3] ? { ...o, status: "ready" as const, assigned: "" } : o); });
    if (method === "DELETE" && path.startsWith("/api/orders/"))
      return patch(s => { s.orders = s.orders.filter(o => o.id !== path.split("/")[3]); });
    if (method === "PATCH" && path.startsWith("/api/orders/")) {
      const oid = path.split("/")[3];
      if (body?.prio !== undefined) return patch(s => { s.orders = s.orders.map(o => o.id === oid ? { ...o, prio: !!body.prio } : o); });
      if (body?.deadline !== undefined) return patch(s => { s.orders = s.orders.map(o => o.id === oid ? { ...o, deadline: String(body.deadline) } : o); });
      return undefined;
    }
    if (method === "POST" && path === "/api/points" && body?.lat !== undefined)
      return patch(s => { s.points = [...(s.points || []), { id: "tmp-" + Date.now(), name: String(body.name || "Точка"), address: String(body.address || "…"), lat: body.lat, lng: body.lng }]; });
    if (method === "POST" && /^\/api\/points\/[^/]+$/.test(path) && body)
      return patch(s => { const pid = path.split("/")[3]; s.points = (s.points || []).map(p => p.id === pid ? { ...p, ...(body.name !== undefined ? { name: String(body.name) } : {}), ...(body.lat !== undefined ? { address: String(body.address || p.address), lat: body.lat, lng: body.lng } : {}) } : p); });
    if (method === "DELETE" && /^\/api\/points\/[^/]+$/.test(path))
      return patch(s => { const pid = path.split("/")[3]; s.points = (s.points || []).filter(p => p.id !== pid); });
    if (method === "POST" && /^\/api\/couriers\/[^/]+\/point$/.test(path))
      return patch(s => { const cid = path.split("/")[3]; s.couriers = s.couriers.map(c => c.id === cid ? { ...c, point_id: String(body?.point_id || "") } : c); });
    if (method === "POST" && path === "/api/couriers" && body?.name)
      return patch(s => { s.couriers.push({ id: "tmp-" + Date.now(), name: String(body.name), status: "base" }); });
    if (method === "DELETE" && path.startsWith("/api/couriers/"))
      return patch(s => { const cid = path.split("/")[3]; const name = s.couriers.find(c => c.id === cid)?.name; s.couriers = s.couriers.filter(c => c.id !== cid); if (name) s.orders = s.orders.map(o => o.assigned === name ? { ...o, status: "ready" as const, assigned: "" } : o); });
    if (method === "POST" && /^\/api\/couriers\/[^/]+\/returned$/.test(path))
      return patch(s => { const cid = path.split("/")[3]; const name = s.couriers.find(c => c.id === cid)?.name; s.couriers = s.couriers.map(c => c.id === cid ? { ...c, status: "base" as const, back_min: 0 } : c); if (name) s.orders = s.orders.filter(o => o.assigned !== name); });
    if (method === "PATCH" && path.startsWith("/api/couriers/")) {
      const cid = path.split("/")[3];
      if (body?.status !== undefined) return patch(s => { s.couriers = s.couriers.map(c => c.id === cid ? { ...c, status: body.status } : c); });
      if (body?.back_min !== undefined) return patch(s => { s.couriers = s.couriers.map(c => c.id === cid ? { ...c, back_min: +body.back_min || 0 } : c); });
      return undefined;
    }
    if (method === "POST" && path === "/api/settings" && body)
      return patch(s => { s.settings = { ...s.settings, ...body as Record<string, number> }; });
    return undefined;
  };
  const mutate = async (method: string, path: string, body?: Record<string, unknown>) => {
    const cur = st;
    const opt = cur ? optimisticFor(method, path, body as Record<string, any> | undefined) : undefined;
    if (opt && cur) setSt(opt(cur));
    try { setSt(await api<AppState>(path, method, body)); }
    catch (e) { showToast((e as Error).message, true); if (opt) void refresh(); }
  };

  /* выдать заказ курьеру напрямую (drag-n-drop из «Готовых заказов») */
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

  const addOrder = async () => {
    const p = pendingOrder.current;
    if (!p) { showToast("Укажите точку: подсказкой в поле или кнопкой 📍 по карте", true); return; }
    await mutate("POST", "/api/orders", { address: orderLabel.current || p.label, lat: p.lat, lng: p.lng, point_id: workPoint });
    pendingOrder.current = null;
    orderLabel.current = "";
    setOrderNote("");
    orderInputRef.current?.focus();
  };

  const solve = async () => {
    if (solving || !st) return;
    setSolving(true);
    try {
      setSt(await api<AppState>("/api/solve", "POST"));
      showToast("Развозка рассчитана");
    } catch (e) { showToast((e as Error).message, true); }
    finally { setSolving(false); }
  };

  /* закрепление за курьером с видимой фазой пересчёта */
  const pinOrder = async (oid: string, cid: string) => {
    if (solving) { showToast("Дождитесь окончания расчёта", true); return; }
    setSolving(true); setPinning(oid);
    try {
      setSt(await api<AppState>("/api/plan/pin", "POST", { order_id: oid, courier_id: cid }));
      showToast("Заказ закреплён за курьером в плане (выдать — кнопкой в маршруте)");
    } catch (e) { showToast((e as Error).message, true); }
    finally { setSolving(false); setPinning(null); }
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

    const include = async () => {
      if (c.status === "off")
        await api("/api/couriers/" + c.id, "PATCH", { status: "base" });
      setSolving(true);
      try {
        setSt(await api<AppState>("/api/solve", "POST", { force: [c.id] }));
        showToast(`«${c.name}» добавлен в план`);
      } catch (e) { showToast((e as Error).message, true); }
      finally { setSolving(false); }
    };

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
    setSolving(true);
    try {
      setSt(await api<AppState>("/api/plan/help", "POST", { courier_id: c.id, point_id: targetPid }));
      showToast(`«${c.name}» приедет на помощь: возьмёт один заказ с точки «${ptName(targetPid)}»`);
    } catch (e) { showToast((e as Error).message, true); }
    finally { setSolving(false); }
  };

  const applyAdvice = async (mode: string) => {
    if (busyMode) return;
    setBusyMode(true);
    try { setSt(await api<AppState>("/api/solve", "POST", { mode })); }
    catch (e) { showToast((e as Error).message, true); }
    finally { setBusyMode(false); }
  };

  const onMapPick = async (ll: { lat: number; lng: number }) => {
    if (!pickTarget) return;
    const target = pickTarget;
    setPickTarget(null);
    try {
      if (target === "point") {
        showToast("Сохраняем точку…");
        const pid = pointEdit;
        const s = pid && pid !== "new"
          ? await api<AppState>("/api/points/" + pid, "POST", { address: "", lat: ll.lat, lng: ll.lng })
          : await api<AppState>("/api/points", "POST", { address: "", lat: ll.lat, lng: ll.lng });
        setSt(s);
        setPointEdit(null);
        pendingPoint.current = null;
        setPointNote("");
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

  const loadHistory = useCallback(async (days: string) => {
    try { setHist(await api("/api/history?days=" + days)); }
    catch (e) { showToast((e as Error).message, true); }
  }, [showToast]);

  const loadWeek = useCallback(async () => {
    try {
      const s = await api<{ days: { day: string; delivered: number; cancelled?: number; avg_cycle_min: number | null }[]; couriers: { courier: string; delivered: number; avg_cycle_min: number | null }[]; on_time?: number; on_time_total?: number }>("/api/stats/week");
      const max = Math.max(1, ...s.days.map(d => d.delivered));
      const days = s.days.length ? s.days.map(d => `
        <div class="wk-row">
          <span class="wk-day">${d.day.slice(5).replace("-", ".")}</span>
          <span class="wk-bar"><i style="width:${Math.round(d.delivered / max * 100)}%"></i></span>
          <span class="wk-num">${d.delivered}</span>
          <span class="wk-cyc">${d.avg_cycle_min != null ? d.avg_cycle_min + " мин" : d.cancelled ? d.cancelled + " отмен" : "–"}</span>
        </div>`).join("") : "";
      const cour = s.couriers.length
        ? `<div style='margin-top:10px;font-size:12px'>` + s.couriers.map(c =>
          `• ${c.courier}: выдано ${c.delivered}${c.avg_cycle_min != null ? ` · цикл ${c.avg_cycle_min} мин` : ""}`).join("<br>") + `</div>` : "";
      const onTime = s.on_time_total ? `<div class="wk-note" style="margin-top:8px">По обещанному времени: ${s.on_time} из ${s.on_time_total}</div>` : "";
      setWeek(days || cour || onTime ? days + cour + onTime : "Пока нет закрытых заказов");
    } catch (e) { setWeek("Не загрузилось: " + (e as Error).message); }
  }, []);

  useEffect(() => {
    if (sheetOpen && sheetTab === "hist") { void loadHistory(histDays); void loadWeek(); }
  }, [sheetOpen, sheetTab]); // eslint-disable-line react-hooks/exhaustive-deps

  /* ---------- производные ---------- */
  const planClockFn = useCallback(() => {
    const base = new Date(st?.plan?.solved_at || Date.now()).getTime();
    return (min: number) => new Date(base + min * 60000)
      .toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  }, [st?.plan?.solved_at]);

  if (!st) {
    const err = stateErr as Error | null;
    return <div style={{ display: "flex", height: "100vh", alignItems: "center", justifyContent: "center", color: "#6d7688" }}>
      {err ? "Ошибка загрузки: " + err.message : stLoading ? "Загрузка…" : "Нет данных"}
    </div>;
  }

  const me: NonNullable<AppState["me"]> = st.me || { id: "", email: "" };
  const today: NonNullable<AppState["today"]> = st.today || {};
  const plan = st.plan;
  const clock = planClockFn();
  const autoP = +(st.settings.auto_prio_min || 0);
  const nowMin = new Date().getHours() * 60 + new Date().getMinutes();
  const readyOrders = st.orders.filter(o => (o.status || "ready") === "ready");
  const outOrders = st.orders.filter(o => o.status === "out");
  const carrying: Record<string, number> = {};
  st.orders.forEach(o => { if (o.status === "out" && o.assigned) carrying[o.assigned] = (carrying[o.assigned] || 0) + 1; });
  const courName = (id: string) => st.couriers.find(c => c.id === id)?.name || "";

  const active = st.couriers.filter(c => c.status !== "off").length;
  const miss = !(st.points || []).length ? "укажите место выдачи заказов"
    : !active ? "добавьте курьера («На базе» или «В пути»)"
    : !st.orders.length ? "добавьте готовые заказы" : "";

  const planLate = (o: Order) => {
    if (!plan || !plan.routes) return 0;
    for (const r of plan.routes) {
      const s = (r.stops || []).find(s => s.order_id === o.id);
      if (s) return s.late_min || 0;
    }
    return 0;
  };

  /* ---------- JSX ---------- */
  return (
    <>
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark"><RouteIcon size={16} strokeWidth={2.25} /></span>
          <span className="brand-name">Диспетчер доставки</span>
        </div>
        {(st?.points?.length || 0) > 0 && (
          <label className="pp-hsel" title="Точка выдачи, в которой вы работаете: новые заказы попадают к курьерам этой точки">
            <MapPin size={13} />
            <span className="pp-hsel-cap">Место работы:</span>
            <select value={workPoint || firstPid} aria-label="Рабочая точка выдачи"
              onChange={e => onWorkPoint(e.target.value)}>
              {st!.points!.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
        )}
        {(!!today.delivered || !!today.cancelled || !!today.avg_cycle_min) && (
          <div className="daystats">
            <span className="dchip ok" title="Доставлено сегодня"><Check size={12} strokeWidth={2.5} />{today.delivered || 0}</span>
            {!!today.cancelled && (
              <span className="dchip no" title="Отменено сегодня"><X size={12} strokeWidth={2.5} />{today.cancelled}</span>
            )}
            {!!today.avg_cycle_min && (
              <span className="dchip" title="Средний цикл заказа"><Timer size={12} />{today.avg_cycle_min} мин</span>
            )}
          </div>
        )}
        <div className="spacer" />
        {undoLen > 0 && (
          <button id="undoBtn" className="iconbtn" style={{ display: "" }}
            title={`Отменить: ${undoStack.current[undoStack.current.length - 1].label} (Ctrl+Z)`}
            aria-label="Отменить действие"
            onClick={() => void doUndo()}><Undo2 size={16} /></button>
        )}
        <button className="iconbtn" title="Тёмная тема" aria-label="Переключить тему"
          onClick={() => applyTheme(!dark)}>{dark ? <Sun size={16} /> : <Moon size={16} />}</button>
        <button className="iconbtn" title="Как пользоваться" aria-label="Справка"
          onClick={() => setHelpOpen(true)}><CircleHelp size={16} /></button>
        <button className="iconbtn gear" title="Параметры расчёта" aria-label="Параметры расчёта"
          onClick={() => { setSheetTab("params"); setSheetOpen(true); }}><Settings size={16} /></button>
        <span className="vdiv" />
        <span className="me" title={me.email || ""}>
          <User size={13} />
          {me.email ? `${me.email}${me.is_admin ? " · админ" : ""}` : ""}
        </span>
        <button id="logoutLink" title="Выйти" aria-label="Выйти" onClick={async () => {
          await fetch("/api/logout", { headers: { Accept: "application/json" } });
          window.location.href = "/login";
        }}><LogOut size={15} /></button>
      </header>

      {pickTarget && (
        <div className="pick-banner">
          📍 Кликните по карте: <b>{pickTarget === "point" ? "точка выдачи сохранится сразу" : "каждый клик добавляет заказ"}</b>
          <button onClick={() => setPickTarget(null)}>Отмена</button>
        </div>
      )}

      <main className="console">
        <aside className="col-left">
          {/* места выдачи заказов */}
          <div className={"acc-item" + (openAcc === "points" ? " open" : "")} data-acc="points">
            <AccHead icon={<MapPin size={15} className="acc-ico" />} label="Места выдачи"
              count={(st.points || []).length} open={openAcc === "points"}
              onClick={() => setOpenAcc(a => a === "points" ? null : "points")} />
            <div className="acc-body"><div className="acc-inner">
          {pointEdit !== null ? (
            <div className="depot-edit">
              <div className="pp-form-title">{pointEdit === "new" ? "Новое место выдачи" : "Изменить место выдачи"}</div>
              <input
                className="pp-name-input" type="text" placeholder="Название (например, ресторан)"
                aria-label="Название места выдачи" value={pointName}
                onChange={e => setPointName(e.target.value)} />
              <div className="addrow">
                <GeoInput
                  key={pointEdit}
                  initial={pointEdit === "new" ? "" : (st.points?.find(p => p.id === pointEdit)?.address || "")}
                  placeholder="Адрес точки (подсказки появятся)" ariaLabel="Адрес места выдачи"
                  onPicked={it => {
                    pendingPoint.current = it;
                    setPointNote(it ? `точка выбрана (${fmtCoords(it)})` : "");
                  }}
                />
                <button className={"iconbtn pick-btn" + (pickTarget === "point" ? " active" : "")}
                  title="Отметить точку кликом по карте" aria-label="Отметить точку по карте"
                  aria-pressed={pickTarget === "point"}
                  onClick={() => setPickTarget(pickTarget === "point" ? null : "point")}><MapPin size={15} /></button>
              </div>
              <div className={"addnote" + (pointNote ? " show" : "")} dangerouslySetInnerHTML={{ __html: pointNote }} />
              <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
                <button className="btn btn-primary" onClick={async () => {
                  const p = pendingPoint.current;
                  if (!p) { showToast("Сначала выберите точку: подсказкой или 📍 по карте", true); return; }
                  const payload = { name: pointName.trim(), address: orderLabelDepot(p), lat: p.lat, lng: p.lng };
                  if (pointEdit === "new") await mutate("POST", "/api/points", payload);
                  else await mutate("POST", "/api/points/" + pointEdit, payload);
                  setPointEdit(null);
                  pendingPoint.current = null;
                  setPointNote("");
                  setPointName("");
                  showToast("Место выдачи сохранено");
                }}>Сохранить</button>
                <button className="btn" onClick={() => { setPointEdit(null); pendingPoint.current = null; setPointNote(""); setPointName(""); }}>Отмена</button>
              </div>
            </div>
          ) : (
            <div className="pts">
              {(st.points || []).map((p, i) => {
                const used = st.couriers.filter(c => (c.point_id || st.points?.[0]?.id) === p.id).length;
                const adminsOn = p.admins || [];
                return (
                  <div className="pp-line" key={p.id}
                    title={`${p.name}: ${p.address} · клик, чтобы изменить`}>
                    <span className="pp-num">{i + 1}</span>
                    <div className="pp-info">
                      <div className="pp-top">
                        <b className="pp-name">{p.name}</b>
                        <span className="pp-stats">
                          <span className="pp-stat" title={`Курьеров на точке: ${used}`}><Users size={10} />{used}</span>
                          <span className={"pp-stat adm" + (adminsOn.length ? " on" : "")}
                            title={adminsOn.length ? `Администраторов онлайн: ${adminsOn.length} (${adminsOn.join(", ")})` : "Администраторов онлайн: нет"}>
                            <ShieldCheck size={10} />{adminsOn.length}
                          </span>
                        </span>
                      </div>
                      <span className="pp-addr">{p.address}</span>
                    </div>
                    <span className="pp-acts">
                      <button className="pp-ib" title="Изменить" aria-label="Изменить место выдачи"
                        onClick={e => {
                          e.stopPropagation();
                          setPointEdit(p.id); setPointName(p.name);
                          setPointNote(`сейчас: ${p.address}`);        // подсказка, что точка уже стоит
                          pendingPoint.current = { label: p.address, lat: p.lat, lng: p.lng };  // сохранение без нового выбора адреса оставит точку на месте
                        }}><Pencil size={13} /></button>
                      {(st.points || []).length > 1 && (
                        <button className="pp-ib danger" title={used > 0 ? `Привязан курьер — сначала перевесьте его` : "Удалить место выдачи"} aria-label="Удалить место выдачи"
                          onClick={async e => {
                            e.stopPropagation();
                            if (used > 0) { showToast(`К точке привязаны курьеры (${used}) — сначала перевесьте их`, true); return; }
                            if (!(await askConfirm(`Удалить «${p.name}»?`, { ok: "Удалить", danger: true }))) return;
                            await mutate("DELETE", "/api/points/" + p.id);
                          }}><Trash2 size={13} /></button>
                      )}
                    </span>
                  </div>
                );
              })}
              <button className="pp-add" title="Добавить место выдачи"
                onClick={() => { setPointEdit("new"); setPointName(""); setPointNote(""); pendingPoint.current = null; }}>добавить место выдачи</button>
            </div>
          )}
            </div></div>
          </div>

          <div className="acc">
            {/* заказы */}
            <div className={"acc-item" + (openAcc === "orders" ? " open" : "")} data-acc="orders">
              <AccHead icon={<Package size={15} className="acc-ico" />} label="Готовые заказы"
                count={st.orders.length} open={openAcc === "orders"}
                onClick={() => setOpenAcc(a => a === "orders" ? null : "orders")} />
              <div className="acc-body"><div className="acc-inner">
                <div className="addrow">
                  <GeoInput
                    inputRef={orderInputRef}
                    placeholder="Адрес (Enter добавит)" ariaLabel="Адрес нового заказа"
                    onPicked={(it, label) => {
                      orderLabel.current = label;
                      if (it) {
                        pendingOrder.current = it;
                        setOrderNote(`точка выбрана <b>(${fmtCoords(it)})</b>. Enter или «+» добавит заказ`);
                        showToast("Точка указана: " + it.label.slice(0, 70));
                      } else {
                        pendingOrder.current = null;
                        setOrderNote("");
                      }
                    }}
                    onEnterEmpty={() => void solve()}
                  />
                  <button className={"iconbtn pick-btn" + (pickTarget === "order" ? " active" : "")}
                    title="Отметить точку кликом по карте" aria-label="Отметить точку по карте"
                    aria-pressed={pickTarget === "order"}
                    onClick={() => setPickTarget(pickTarget === "order" ? null : "order")}><MapPin size={15} /></button>
                  <button className="plus" title="Добавить заказ" aria-label="Добавить заказ" onClick={() => void addOrder()}><Plus size={15} /></button>
                </div>
                <div className={"addnote" + (orderNote ? " show" : "")} dangerouslySetInnerHTML={{ __html: orderNote }} />
                <div id="orderList" className="ents" onMouseLeave={() => setHoverOid(null)}>
                  {readyOrders.length === 0 && !outOrders.length && (
                    <div className="empty-state">
                      <PackageOpen size={22} />
                      <b>Готовых заказов нет</b>
                      <span>Введите адрес выше или отметьте точку на карте</span>
                    </div>
                  )}
                  {readyOrders.map((o, i) => {
                    void tick;
                    const age = orderAgeMin(o);
                    const auto = autoP > 0 && age >= autoP && !o.prio;
                    const late = planLate(o);
                    const soon = !late && o.deadline
                      ? (() => { const [h, m] = o.deadline.split(":").map(Number); return h * 60 + m - nowMin; })()
                      : null;
                    return (
                      <div
                        key={o.id}
                        className={`ent ocard${o.prio ? " prio" : ""}${late > 0 ? " burning" : ""}${cardHl === o.id ? " hl" : ""}${pinning === o.id ? " adding" : ""}`}
                        data-oid={o.id}
                        draggable={pinning !== o.id}
                        title={`${o.address} · перетащите на курьера, чтобы выдать сразу`}
                        onDragStart={e => {
                          e.dataTransfer.setData("text/plain", "assign:" + o.id);
                          e.dataTransfer.effectAllowed = "move";
                        }}
                        onMouseEnter={() => setHoverOid(o.id)}
                      >
                        <span className="e-num">{pinning === o.id ? <Loader2 size={13} className="spin" /> : i + 1}</span>
                        <span className="e-name">
                          {o.address}
                          {pinning === o.id && <span className="pin-chip">пересчитываем маршрут…</span>}
                          {(st.points?.length || 0) > 1 && o.point_id && st.points?.some(p => p.id === o.point_id) && (
                            <span className="opt-tag" title="Место выдачи заказа">{st.points.find(p => p.id === o.point_id)!.name}</span>
                          )}
                          {o.deadline && <span className="dl-chip" title="Обещали к этому времени"><Timer size={11} /> {o.deadline}</span>}
                          {late > 0
                            ? <span className="burn-chip" title="По текущему плану к обещанному времени не успеваем"><Flame size={11} /> опоздание ~{late} мин</span>
                            : (soon !== null && 0 <= soon && soon <= 15)
                              ? <span className="soon-chip" title="Дедлайн на подходе, а заказа ещё нет в маршруте"><Hourglass size={11} /> скоро {o.deadline}</span>
                              : null}
                          {o.prio && <span className="prio-tag"><Zap size={11} /> приоритет</span>}
                          {auto && <span className="age-tag" title="Долго в очереди: в плане будет как приоритетный"><Hourglass size={11} /> {age} мин</span>}
                          {dlEdit === o.id && (
                            <DlPop
                              initial={o.deadline || dlRound(new Date(Date.now() + 30 * 60000))}
                              hasDeadline={!!o.deadline}
                              onSave={async val => {
                                  await mutate("PATCH", "/api/orders/" + o.id, { deadline: val });
                                setDlEdit(null);
                              }}
                              onClose={() => setDlEdit(null)}
                            />
                          )}
                        </span>
                        <span className="e-acts">
                          <button title="Обещанное время доставки (дедлайн)" aria-label="Дедлайн заказа"
                            onClick={() => setDlEdit(dlEdit === o.id ? null : o.id)}><Timer size={14} /></button>
                          <button className={"prio-btn" + (o.prio ? " on" : "")}
                            title={o.prio ? "Снять приоритет" : "Приоритет: доставить как можно раньше"}
                            aria-label="Приоритет заказа"
                            onClick={() => void mutate("PATCH", "/api/orders/" + o.id, { prio: !o.prio })}><Zap size={14} /></button>
                          <button className="ok" title="Выдать курьеру (из текущего плана)" aria-label="Выдать заказ"
                            onClick={async () => {
                              const r = (st.plan?.routes || []).find(r => (r.stops || []).some(s => s.order_id === o.id));
                              if (!r) { showToast("Заказа нет в текущем плане: рассчитайте план или выдайте с маршрута", true); return; }
                              await mutate("POST", "/api/orders/assign", { order_ids: [o.id], courier_id: r.courier_id });
                              undoToast(`Выдан: ${r.courier_name}`, `выдача ${o.address || ""}`.slice(0, 60), "assign", { order_ids: [o.id] });
                            }}><Check size={14} /></button>
                          <button className="no" title="Отменить (в историю)" aria-label="Отменить заказ"
                            onClick={async () => {
                              await mutate("DELETE", "/api/orders/" + o.id, { outcome: "cancelled" });
                              pushUndo(`удаление ${o.address || ""}`.slice(0, 60), "delOrder",
                                { address: o.address, lat: o.lat, lng: o.lng, prio: o.prio, deadline: o.deadline, point_id: o.point_id });
                              showToast("Заказ отменён", false, { label: "Отменить", fn: () => void doUndo() });
                            }}><X size={14} /></button>
                        </span>
                      </div>
                    );
                  })}
                  {outOrders.length > 0 && <div className="out-cap"><Bike size={13} /> В развозке</div>}
                  {outOrders.map(o => (
                    <div key={o.id} className="ent ocard out" data-oid={o.id} title={o.address}
                      onMouseEnter={() => setHoverOid(o.id)}>
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
                </div></div>
              </div>
            </div>

            {/* курьеры */}
            <div className={"acc-item" + (openAcc === "couriers" ? " open" : "")} data-acc="couriers">
              <AccHead icon={<Bike size={15} className="acc-ico" />} label="Курьеры"
                count={st.couriers.length} open={openAcc === "couriers"}
                onClick={() => setOpenAcc(a => a === "couriers" ? null : "couriers")} />
              <div className="acc-body"><div className="acc-inner">
                <div className="addrow">
                  <input type="text" placeholder="Имя курьера (Enter)" aria-label="Имя нового курьера"
                    value={courierName} onChange={e => setCourierName(e.target.value)}
                    onKeyDown={async e => {
                      if (e.key !== "Enter") return;
                      const name = courierName.trim();
                      if (!name) { showToast("Введите имя курьера", true); return; }
                      setCourierName("");
                      await mutate("POST", "/api/couriers", { name });
                    }} />
                  <button className="plus" title="Добавить курьера" aria-label="Добавить курьера"
                    onClick={async () => {
                      const name = courierName.trim();
                      if (!name) { showToast("Введите имя курьера", true); return; }
                      setCourierName("");
                      await mutate("POST", "/api/couriers", { name });
                    }}><Plus size={15} /></button>
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
                      <div key={c.id}
                        className={"ent crow" + (dragOverCourier === c.id ? " drop-hint" : "")}
                        draggable
                        title={`${c.name}: перетащите в план развозки справа, чтобы включить в расчёт`}
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
                          <span className="seg" role="group" aria-label="Статус курьера">
                            {(["base", "away", "off"] as const).map(s => (
                              <button key={s} className={c.status === s ? "on-" + s : ""}
                                title={SEG_TITLES[s]} aria-label={"Статус: " + SEG_TITLES[s]}
                                onClick={() => { if (c.status !== s) void mutate("PATCH", "/api/couriers/" + c.id, { status: s }); }}>
                                {SEG_ICONS[s]}
                              </button>
                            ))}
                          </span>
                          <span className="e-acts">
                            <button className="no" title="Удалить курьера" aria-label="Удалить курьера"
                              onClick={async () => {
                                if (!(await askConfirm(`Удалить курьера «${c.name}»?`, { ok: "Удалить", danger: true }))) return;
                                await mutate("DELETE", "/api/couriers/" + c.id);
                                pushUndo(`курьер ${c.name}`, "delCourier", { name: c.name });
                              }}><X size={14} /></button>
                          </span>
                        </div>
                        {(st.points || []).length > 0 && (
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
                          ? <div className="c-row2 geo-row" title="Возврат рассчитан по живой геолокации курьера (обновляется автоматически)">
                              {c.geo.at_depot
                                ? <><MapPin size={11} /> у своей точки — готов грузиться</>
                                : <><Timer size={11} /> вернётся ≈{c.geo.back_min} мин (по гео)</>}
                            </div>
                          : <div className="c-row2 geo-row"><Timer size={11} /> вернётся через
                            <input className="backMin" type="number" min={0} max={480} defaultValue={c.back_min ?? 15}
                              title="Через сколько минут вернётся на базу (привяжите Telegram — будет считаться сам)" aria-label="Возврат на базу, минут"
                              onChange={e => void mutate("PATCH", "/api/couriers/" + c.id, { back_min: +e.target.value || 0 })} />
                            мин
                          </div>)}
                        {c.geo?.at_order && (
                          <div className="c-row2 geo-row" title="Курьер сейчас стоит у этого заказа">
                            <Bike size={11} /> у заказа: {c.geo.at_order}
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
                                <MapPin size={11} /> {posAgeMin(c.pos.ts)} назад{c.pos.live ? " · live" : ""}
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
          />
          <button className="fit-btn" title="Показать все точки в кадре"
            style={{ position: "absolute", right: 12, bottom: 12, zIndex: 800 }}
            onClick={() => setFitSignal(s => s + 1)}>⤢</button>
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

      {/* «Ещё» */}
      {sheetOpen && (
        <Sheet
          st={st}
          tab={sheetTab}
          setTab={setSheetTab}
          hist={hist} histDays={histDays}
          onHistDays={d => { setHistDays(d); void loadHistory(d); }}
          week={week}
          pwOld={pwOld} pwNew={pwNew} setPwOld={setPwOld} setPwNew={setPwNew}
          onChangePw={async () => {
            try {
              await api("/api/password", "POST", { old: pwOld, new: pwNew });
              setPwOld(""); setPwNew("");
              showToast("Пароль изменён");
            } catch (e) { showToast((e as Error).message, true); }
          }}
          userEmail={userEmail} userPwd={userPwd} userIsAdmin={userIsAdmin}
          setUserEmail={setUserEmail} setUserPwd={setUserPwd} setUserIsAdmin={setUserIsAdmin}
          onAddUser={async () => {
            try {
              const s = await api<AppState>("/api/users", "POST", { email: userEmail, password: userPwd, is_admin: userIsAdmin });
              setSt(s);
              setUserEmail(""); setUserPwd(""); setUserIsAdmin(false);
              showToast("Пользователь добавлен");
            } catch (e) { showToast((e as Error).message, true); }
          }}
          onDelUser={async (uid, email) => {
            if (!(await askConfirm(`Удалить пользователя «${email}»?`, { ok: "Удалить", danger: true }))) return;
            try { setSt(await api<AppState>("/api/users/" + uid, "DELETE")); }
            catch (e) { showToast((e as Error).message, true); }
          }}
          onSaveSettings={async (s: Record<string, number | boolean>) => {
            try {
              setSt(await api<AppState>("/api/settings", "POST", s));
              showToast("Параметры сохранены");
            } catch (e) { showToast((e as Error).message, true); }
          }}
          onClose={() => setSheetOpen(false)}
        />
      )}

      {/* справка */}
      {helpOpen && (
        <div className="help-overlay" onClick={e => { if (e.target === e.currentTarget) setHelpOpen(false); }}>
          <div className="help-card">
            <button className="close" style={{ float: "right", border: "none", background: "transparent", fontSize: 16, cursor: "pointer", color: "#6d7688" }}
              aria-label="Закрыть" onClick={() => setHelpOpen(false)}>✕</button>
            <h3>Как пользоваться</h3>
            <ol>
              <li>Поставьте <b>точку ресторана</b>: строка сверху, адрес с подсказками. Или кликните по карте.</li>
              <li>Добавьте <b>курьеров</b> и отметьте, кто сейчас «На базе».</li>
              <li>Занесите <b>готовые заказы</b>: адресом с подсказками или кликом по карте.</li>
              <li>Нажмите <b>«Рассчитать развозку»</b>. Карточка «отдать сейчас» и есть задание курьеру на базе.</li>
            </ol>
            <p className="note">План пересчитывается сам после изменений. Редкие настройки, история и доступ — шестерёнка в шапке.</p>
          </div>
        </div>
      )}

      {/* привязка Telegram */}
      {bindFor && st && (
        <BindModal
          courier={bindFor}
          bot={st.tg?.bot || ""}
          seen={st.tg?.seen || []}
          onDone={s => { setSt(s); setBindFor(null); }}
          onClose={() => setBindFor(null)}
        />
      )}

      {/* подтверждение */}
      {ask && (
        <div className="help-overlay" role="dialog" aria-modal="true"
          onClick={e => { if (e.target === e.currentTarget) { setAsk(null); ask.resolve(false); } }}>
          <div className="help-card ask-card">
            <h3>{ask.text}</h3>
            <div className="ask-btns">
              <button className="btn" autoFocus onClick={() => { setAsk(null); ask.resolve(false); }}>Отмена</button>
              <button className={"btn " + (ask.danger ? "danger" : "btn-primary")}
                onClick={() => { setAsk(null); ask.resolve(true); }}>{ask.ok}</button>
            </div>
          </div>
        </div>
      )}

      {/* тост */}
      <div id="toast" className={(toast?.err ? "err " : "") + (toast ? "show" : "")}>
        <span>{toast?.msg}</span>
        {toast?.act && (
          <button id="toastAct" onClick={() => { setToast(null); toast.act!.fn(); }}>{toast.act.label}</button>
        )}
      </div>
    </>
  );
}

/* ---------- привязка Telegram ---------- */
function BindModal({ courier, bot, seen, onDone, onClose }: {
  courier: Courier; bot: string;
  seen: { chat_id: string; login: string; ts: number }[];
  onDone: (s: AppState) => void; onClose: () => void;
}) {
  const [manual, setManual] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const bind = async (chat_id: string, login?: string) => {
    if (!/^\d+$/.test(chat_id)) { setErr("ID должен быть числом"); return; }
    setBusy(true); setErr("");
    try {
      const s = await api<AppState>(`/api/couriers/${courier.id}/bind`, "POST", { chat_id, login });
      onDone(s);
    } catch (e) {
      setErr(String((e as Error).message || e));
    } finally { setBusy(false); }
  };
  const unbind = async () => {
    setBusy(true);
    try { onDone(await api<AppState>(`/api/couriers/${courier.id}/unbind`, "POST")); }
    finally { setBusy(false); }
  };

  return (
    <div className="help-overlay" role="dialog" aria-modal="true"
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="help-card bind-card">
        <button className="close" style={{ float: "right", border: "none", background: "transparent", fontSize: 16, cursor: "pointer", color: "#6d7688" }}
          aria-label="Закрыть" onClick={onClose}>✕</button>
        <h3>🔗 Telegram · {courier.name}</h3>
        {courier.tg_chat_id && (
          <p className="note">Привязан: <b>{courier.tg_login || "ID " + courier.tg_chat_id}</b></p>
        )}
        <p className="note">
          Курьер пишет боту {bot || "(бот не отвечает, проверьте токен)"} команду{" "}
          <b>/start</b> и нажимает «Геолокация» (или включает live-трансляцию).
          Его ID появится в списке ниже — привяжите его к курьеру.
        </p>
        {seen.length > 0 && (
          <div className="bind-list">
            {seen.map(u => (
              <button key={u.chat_id} disabled={busy} className="bind-user"
                title={u.chat_id}
                onClick={() => void bind(u.chat_id, u.login)}>
                <span>@{u.login}</span>
                <small>ID {u.chat_id} · {posAgeMin(u.ts) < 1 ? "только что" : posAgeMin(u.ts) + " мин назад"}</small>
              </button>
            ))}
          </div>
        )}
        <div className="bind-manual">
          <input placeholder="ID вручную (число)" value={manual} inputMode="numeric"
            onChange={e => setManual(e.target.value.replace(/\D/g, ""))} />
          <button className="btn btn-primary" disabled={busy || !manual} onClick={() => void bind(manual)}>Привязать</button>
        </div>
        {err && <p className="note" style={{ color: "#b3261e" }}>{err}</p>}
        {courier.tg_chat_id && (
          <button className="btn danger" disabled={busy} onClick={() => void unbind()}>Отвязать</button>
        )}
      </div>
    </div>
  );
}

/* ---------- попап дедлайна ---------- */
function DlPop({ initial, hasDeadline, onSave, onClose }: {
  initial: string; hasDeadline: boolean;
  onSave: (val: string) => Promise<void>; onClose: () => void;
}) {
  const [val, setVal] = useState(initial);
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node) &&
        !(e.target as HTMLElement).closest("[title='Обещанное время доставки (дедлайн)']")) onClose();
    };
    document.addEventListener("click", h);
    return () => document.removeEventListener("click", h);
  }, [onClose]);
  const shift = (n: number) => {
    const [h, m] = val.split(":").map(Number);
    setVal(`${String((h + Math.floor((m + n) / 60) + 24) % 24).padStart(2, "0")}:${String((m + n + 60) % 60).padStart(2, "0")}`);
  };
  const setPart = (part: "hh" | "mm", raw: string) => {
    const d = raw.replace(/\D/g, "").slice(0, 2);
    const [h = "00", m = "00"] = val.split(":");
    setVal(part === "hh" ? `${(d || "0").padStart(2, "0")}:${m}` : `${h}:${(d || "0").padStart(2, "0")}`);
  };
  const save = () => {
    let [h, m] = val.split(":").map(Number);
    setVal(`${String(Math.min(23, Math.max(0, h || 0))).padStart(2, "0")}:${String(Math.min(59, Math.max(0, m || 0))).padStart(2, "0")}`);
    void onSave(`${String(Math.min(23, Math.max(0, h || 0))).padStart(2, "0")}:${String(Math.min(59, Math.max(0, m || 0))).padStart(2, "0")}`);
  };
  return (
    <div className="dl-pop" ref={ref} onClick={e => e.stopPropagation()}>
      <div className="dl-row">
        {[15, 30, 45, 60, 90].map(n => (
          <button key={n} className="dl-q" onClick={() => void onSave(dlRound(new Date(Date.now() + n * 60000)))}>
            {n === 60 ? "1 ч" : n === 90 ? "1.5 ч" : "+" + n}
          </button>
        ))}
        <span className="dl-ql">мин от сейчас</span>
      </div>
      <div className="dl-row">
        <button className="dl-tbtn" title="На 5 минут раньше" onClick={() => shift(-5)}>−5</button>
        <input className="dl-hh" maxLength={2} inputMode="numeric" aria-label="Часы" value={val.split(":")[0]}
          onChange={e => setPart("hh", e.target.value)} />
        <span className="dl-colon">:</span>
        <input className="dl-mm" maxLength={2} inputMode="numeric" aria-label="Минуты" value={val.split(":")[1]}
          onChange={e => setPart("mm", e.target.value)} />
        <button className="dl-tbtn" title="На 5 минут позже" onClick={() => shift(5)}>+5</button>
        <span className="dl-flex" />
        <button className="dl-ok" title="Сохранить дедлайн" onClick={save}>✓</button>
        {hasDeadline && <button className="dl-x" title="Убрать дедлайн" onClick={() => void onSave("")}>✕</button>}
      </div>
    </div>
  );
}

/* ---------- панель плана ---------- */
function PlanPanel({ st, clock, busyMode, onMode, onGive, onCopy, onTg, dragOverRoute, setDragOverRoute, onMoveStop, onPin }: {
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
  let prov = plan.routing === "roads" ? "по дорогам · " + (({ ORS: "ORS", OSRM: "OSRM" } as Record<string, string>)[plan.provider || ""] || "") : "оценка по прямой";
  const ors = st.ors || {};
  if (plan.provider === "ORS") prov += ors.paused ? " · пауза" : ` · квота ${ors.used ?? "?"}/${ors.soft_limit ?? "?"}`;
  if (plan.unassigned) prov += ` · без маршрута: ${plan.unassigned}`;

  return (
    <>
      <div className="plan-top">
        <div className="pt-label">Последняя доставка</div>
        <div className="pt-clock">≈{plan.last_delivery_clock || "?"} <small>+{plan.last_delivery_min} мин</small></div>
        <div className="pt-sub">
          {prov} · рассчитано {(plan.solved_at || "").replace("T", " ").slice(11, 16)}
          {plan.stale && " · ⟳ устарел — нажмите «Рассчитать»"}
          {plan.moved && " · ✋ правка вручную"}
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
              {r.status === "base"
                ? <span className="chip chip-green">ОТДАТЬ СЕЙЧАС</span>
                : <span className="chip chip-amber">следующим</span>}
              {r.start_delay_min > 0 && (
                <span className="chip chip-amber" title="Курьер ещё в пути, маршрут сдвинут на время возврата">
                  старт +{r.start_delay_min} мин
                </span>
              )}
              {giveIds.length > 0 && (
                <button className="r-give" onClick={() => onGive(r)}
                  title={`Отметить выданным: ${giveIds.length} заказ(ов) уйдут в развозку, остальные маршруты останутся как есть`}>
                  ✓ Выдать ({giveIds.length})
                </button>
              )}
              {st.cfg?.tg && r.tg_chat_id && (
                <button className="r-tg" title="Отправить маршрут курьеру в Telegram" onClick={() => onTg(r.courier_id)}>📤</button>
              )}
              <button className="r-copy" title="Скопировать маршрут текстом, чтобы отправить курьеру" onClick={() => onCopy(r)}>📋</button>
            </div>
            <div className="r-sub">
              {r.count} заказ(ов) · вернётся ≈{clock(r.total_min)}{r.distance_km ? ` · ${r.distance_km} км` : ""}
            </div>
            {r.trips.map((tr, ti) => (
              <div key={ti}>
                {r.trips.length > 1 && (
                  <div className="trip-head">
                    Заезд {ti + 1} · старт ≈{tr.start_clock || clock(tr.start_delay_min)}{tr.start_delay_min ? ` (+${tr.start_delay_min} мин)` : ""}
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
                            <span className="s-prio" title={`Приоритетный${s.auto ? ", поднялся сам по возрасту" : ""}`}>⚡</span>
                          )} {s.address} {s.deadline && <span className="s-dl">⏱{s.deadline}</span>}
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

/* ---------- совет «ждать/не ждать» ---------- */
function AdviceCard({ a, busy, onMode }: { a: Advice; busy: boolean; onMode: (m: string) => void; }) {
  const nm = declName(a.wait_couriers.map(w => w.name).join(", "));
  const lastBack = a.wait_couriers.map(w => w.back_clock).sort().pop();
  const backTxt = a.wait_couriers.length === 1 ? `вернётся ≈${lastBack}` : `до ≈${lastBack}`;
  const delta = a.gain_last_min | 0;
  const rel = a.chosen === "split" ? -delta : delta;
  const good = rel < 0;
  const verdict = rel === 0 ? "разницы нет" : good ? `выгодно: −${Math.abs(rel)} мин` : `дороже: +${rel} мин`;
  const list = a.held.map(h => shortAddr(h.address));
  const row = (id: string, title: string, side: { counts: string; last_clock?: string; avg_min: number }) => (
    <button className={"adv-row" + (a.chosen === id ? " chosen" : "")} title="Применить этот сценарий" onClick={() => onMode(id)}>
      <span className="adv-dot" />
      <span className="adv-t">{title}{a.chosen === id && <span className="adv-done">✓</span>}</span>
      <span className="adv-meta">{side.counts}</span>
      <span className="adv-nums">≈{side.last_clock || "?"} · ср {side.avg_min}</span>
    </button>
  );
  return (
    <div className={"advice" + (busy ? " busy" : "")} title="Сравнение сценариев: всё курьерам на базе сейчас или разделить с возвращающимся">
      <div className="adv-head">
        <span className="adv-q">⚖ Ждать {nm}? <span className="adv-back">{backTxt}</span></span>
        <span className={"adv-verdict " + (rel === 0 ? "no" : good ? "ok" : "no")}
          title="Разница со вторым сценарием по времени последней доставки">{verdict}</span>
      </div>
      {row("now", "Не ждать", a.now)}
      {row("split", "Ждать", a.split)}
      {list.length > 0 && (
        <div className="adv-held" title={list.join("\n")}>
          📎 {declName(a.held[0].courier)}: {list.slice(0, 3).join(" · ")}{list.length > 3 ? ` …ещё ${list.length - 3}` : ""}
        </div>
      )}
    </div>
  );
}

/* ---------- «Ещё» ---------- */
const SET_FIELDS: { key: string; label: string; min: number; max: number; step?: number; title?: string }[] = [
  { key: "speed_kmh", label: "Скорость, км/ч", min: 5, max: 120 },
  { key: "handover_min", label: "Вручение, мин", min: 0, max: 60 },
  { key: "max_orders", label: "Макс. заказов", min: 1, max: 50 },
  { key: "traffic", label: "Пробки, ×", min: 1, max: 3, step: 0.05, title: "Надбавка к дорожному времени: 1 = свободно, 1.25 = средняя загрузка, 1.5–2 = час пик" },
  { key: "lights_sec_per_km", label: "Светофоры, с/км", min: 0, max: 60, title: "Средняя задержка на светофорах: секунд на километр пути" },
  { key: "auto_prio_min", label: "Авто-приоритет, мин (0 = выкл)", min: 0, max: 240, title: "Заказ ждёт в очереди дольше этого времени — он сам становится приоритетным" },
  { key: "reload_min", label: "Перезагрузка, мин", min: 0, max: 120, title: "Время на базе между заездами: принять заказы, погрузиться" },
  { key: "approach_center_min", label: "Подъезд: центр, мин", min: 0, max: 15, title: "Добавка на парковку и подъём к двери: в радиусе 2.5 км от места выдачи" },
  { key: "approach_far_min", label: "Подъезд: окраины, мин", min: 0, max: 15, title: "Добавка на парковку и подъём к двери за пределами 2.5 км от места выдачи" },
];

function Sheet(p: {
  st: AppState;
  tab: "params" | "hist" | "users";
  setTab: (t: "params" | "hist" | "users") => void;
  hist: { rows?: any[]; summary?: Record<string, number | null> } | null;
  histDays: string;
  onHistDays: (d: string) => void;
  week: string;
  pwOld: string; pwNew: string; setPwOld: (v: string) => void; setPwNew: (v: string) => void;
  onChangePw: () => Promise<void>;
  userEmail: string; userPwd: string; userIsAdmin: boolean;
  setUserEmail: (v: string) => void; setUserPwd: (v: string) => void; setUserIsAdmin: (v: boolean) => void;
  onAddUser: () => Promise<void>;
  onDelUser: (uid: string, email: string) => Promise<void>;
  onSaveSettings: (s: Record<string, number | boolean>) => Promise<void>;
  onClose: () => void;
}) {
  const s = p.st.settings;
  const set = async (patch: Record<string, number | boolean>) => {
    const full: Record<string, number | boolean> = {
      speed_kmh: s.speed_kmh, handover_min: s.handover_min, max_orders: s.max_orders,
      traffic: s.traffic, lights_sec_per_km: s.lights_sec_per_km,
      auto_prio_min: s.auto_prio_min, reload_min: s.reload_min,
      approach_center_min: s.approach_center_min ?? 4, approach_far_min: s.approach_far_min ?? 2,
      hour_traffic: s.hour_traffic ? 1 : 0,
      ...patch,
    };
    await p.onSaveSettings(full);
  };
  const summ = p.hist?.summary || {};
  const TITLES = { params: "Параметры расчёта", hist: "Неделя и история", users: "Доступ" } as const;
  return (
    <div className="sheet-bg" onClick={e => { if (e.target === e.currentTarget) p.onClose(); }}>
      <div className="sheet">
        <button className="close" aria-label="Закрыть" onClick={p.onClose}>✕</button>
        <h3>{TITLES[p.tab]}</h3>
        <div className="sheet-tabs" role="tablist">
          <button role="tab" aria-selected={p.tab === "params"} className={p.tab === "params" ? "on" : ""}
            onClick={() => p.setTab("params")}><SlidersHorizontal size={14} />Параметры</button>
          <button role="tab" aria-selected={p.tab === "hist"} className={p.tab === "hist" ? "on" : ""}
            onClick={() => p.setTab("hist")}><History size={14} />Неделя и история</button>
          <button role="tab" aria-selected={p.tab === "users"} className={p.tab === "users" ? "on" : ""}
            onClick={() => p.setTab("users")}><Users size={14} />Доступ</button>
        </div>

        {p.tab === "params" && (<>
        <div className="settings-grid">
          {SET_FIELDS.map(f => (
            <label key={f.key} title={f.title}>{f.label}
              <input type="number" min={f.min} max={f.max} step={f.step || 1}
                defaultValue={s[f.key] as number}
                key={f.key + String(s[f.key])}
                onChange={e => void set({ [f.key]: +e.target.value })} />
            </label>
          ))}
          <label title="Коэффициент пробок по часам суток (утренний и вечерний пик)"
            style={{ gridColumn: "1/-1", flexDirection: "row", alignItems: "center", gap: 8 }}>
            <input type="checkbox" style={{ width: "auto" }} checked={!!s.hour_traffic}
              onChange={e => void set({ hour_traffic: e.target.checked ? 1 : 0 })} />
          учитывать час пик (утро/вечер)
        </label>
        </div>
        </>)}

        {p.tab === "hist" && (<>
        <h4>Неделя</h4>
        <div className="wk-note" dangerouslySetInnerHTML={{ __html: p.week || "Загрузка…" }} />
        <div style={{ marginTop: 8 }}>
          <a href="/report/day" target="_blank" rel="noopener" className="btn"
            style={{ fontSize: "12.5px" }} title="Печатная версия: Ctrl+P позволяет сохранить в PDF">Отчёт дня (PDF)</a>
        </div>

        <h4>История</h4>
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
          <select style={{ border: "1px solid var(--line2)", borderRadius: 7, padding: "6px 8px", background: "var(--panel)", color: "var(--ink)" }}
            value={p.histDays} onChange={e => p.onHistDays(e.target.value)}>
            <option value="1">сегодня</option>
            <option value="2">2 дня</option>
            <option value="7">7 дней</option>
            <option value="31">31 день</option>
          </select>
          <a href={"/api/history/export?days=" + p.histDays} className="btn" style={{ fontSize: "12.5px" }}
            title="Скачать CSV (открывается в Excel)">CSV</a>
        </div>
        <div style={{ color: "var(--mut)", fontSize: "12.5px" }}>
          Выдано: <b>{summ.delivered || 0}</b> · Отменено: <b>{summ.cancelled || 0}</b>
          {summ.avg_cycle_min != null && <> · Средний цикл: <b>{summ.avg_cycle_min} мин</b></>}
        </div>
        <div className="hist-list">
          {p.hist?.rows?.length
            ? p.hist.rows.map((r: any, i: number) => (
              <div className="hrow" key={i}>
                <span className="h-time">{(r.closed_at || "").replace("T", " ").slice(5, 16)}</span>
                <span className="h-addr" title={r.address}>{r.address || ""}</span>
                <span className="h-cour">{r.courier || ""}</span>
                <span className={r.outcome === "delivered" ? "h-ok" : "h-no"}>{r.outcome === "delivered" ? "✓" : "✕"}</span>
                <span className="h-cyc">{r.cycle_min != null ? r.cycle_min + " мин" : ""}</span>
              </div>
            ))
            : <div className="empty-list">Пока пусто</div>}
        </div>
        </>)}

        {p.tab === "users" && (<>
        <div className="col">
          <label style={{ fontSize: "11.5px", color: "var(--mut)" }}>Смена своего пароля</label>
          <input type="password" placeholder="старый пароль" aria-label="Старый пароль"
            value={p.pwOld} onChange={e => p.setPwOld(e.target.value)} />
          <input type="password" placeholder="новый (мин. 4 символа)" aria-label="Новый пароль"
            value={p.pwNew} onChange={e => p.setPwNew(e.target.value)} />
          <button className="btn" onClick={() => void p.onChangePw()}>Сменить пароль</button>
        </div>
        {!!p.st.me?.is_admin && (
          <div className="col" style={{ marginTop: 14 }}>
            <label style={{ fontSize: "11.5px", color: "var(--mut)" }}>Новый пользователь (только администратор)</label>
            <input type="email" placeholder="email, напр. ivan@cafe.by" aria-label="Email нового пользователя"
              value={p.userEmail} onChange={e => p.setUserEmail(e.target.value)} />
            <input type="password" placeholder="начальный пароль" aria-label="Начальный пароль"
              value={p.userPwd} onChange={e => p.setUserPwd(e.target.value)} />
            <label style={{ fontSize: 12, color: "var(--mut)", display: "flex", gap: 6, alignItems: "center" }}>
              <input type="checkbox" style={{ width: "auto" }} checked={p.userIsAdmin}
                onChange={e => p.setUserIsAdmin(e.target.checked)} /> администратор
            </label>
            <button className="btn btn-primary" onClick={() => void p.onAddUser()}>Добавить пользователя</button>
            <div style={{ marginTop: 10 }}>
              {(p.st.users || []).map(u => (
                <div className="user-row" key={u.id}>
                  <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }} title={u.email}>{u.email}</span>
                  {!!u.is_admin && <span className="chip chip-green">админ</span>}
                  <button style={{ opacity: 1, border: "none", background: "transparent", color: "#c02626", cursor: "pointer", fontSize: 12 }}
                    disabled={u.id === p.st.me?.id}
                    title={u.id === p.st.me?.id ? "себя удалить нельзя" : "Удалить пользователя"}
                    onClick={() => void p.onDelUser(u.id, u.email)}>✕</button>
                </div>
              ))}
            </div>
            <div style={{ marginTop: 12 }}>
              <a href="/api/backup" className="btn" title="Скачать снимок базы данных">Бэкап БД</a>
            </div>
          </div>
        )}
        </>)}
      </div>
    </div>
  );
}

/* адрес депо: подпись из GeoInput или ручной текст */
function orderLabelDepot(p: GeoItem) { return p.label; }
