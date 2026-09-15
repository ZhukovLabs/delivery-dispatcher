"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, fmtCoords, type AppState, type Courier, type Order, type Route, type Advice } from "@/lib/api";
import GeoInput, { type GeoItem } from "./GeoInput";
import { ArrowRight, Bike, ChartColumn, Check, ChevronDown, CircleHelp, ClipboardCopy, Clock, Download, FileSpreadsheet, FileText, Flame, Gauge, Hand, Hourglass, House, Link2, Loader2, LogOut, MapPin, Moon, Package, PackageOpen, Paperclip, Pause, Pencil, Plus, RefreshCw, Route as RouteIcon, Scale, Send, Settings, ShieldCheck, SlidersHorizontal, Sun, Timer, Trash2, TriangleAlert, Undo2, Unlink, User, Users, X, Zap } from "lucide-react";

const MapView = dynamic(() => import("./MapView"), {
  ssr: false,
  loading: () => <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center", color: "#6d7688" }}>карта загружается…</div>,
});

/* ---------- утилиты ---------- */
const AVAS: [string, string][] = [
  ["#dbe7fb", "#2c5a9e"], ["#e9e2fb", "#5f47a5"], ["#fbe3f0", "#a33a75"], ["#dcf3f0", "#13756c"],
  ["#fbeed3", "#8f5d0a"], ["#e2f4ea", "#0d7041"], ["#fbe3e0", "#a04233"], ["#e3e7fb", "#474ca3"],
  ["#eef6d8", "#5a741e"], ["#ddf1fa", "#1a6784"],
];

/** Данные /api/stats/week — недельная статистика для вкладки «Статистика». */
type WeekStats = {
  days: { day: string; delivered: number; cancelled?: number; avg_cycle_min: number | null }[];
  couriers: { courier: string; delivered: number; avg_cycle_min: number | null }[];
  on_time?: number; on_time_total?: number;
};
/** Строка истории заказов из /api/history. */
type HistRow = { closed_at?: string; address?: string; courier?: string; outcome?: string; cycle_min?: number | null };
type HistData = { rows?: HistRow[]; summary?: Record<string, number | null> } | null;

/** Инициалы для аватара: «Настя» -> «Н», «Анна Петрова» -> «АП», fallback — первая буква email. */
function initialsOf(name: string, email: string) {
  const n = (name || "").trim();
  if (!n) return (email[0] || "?").toUpperCase();
  const w = n.split(/\s+/);
  return (w[0][0] + (w[1] ? w[1][0] : "")).toUpperCase();
}

/** Стабильный пастельный цвет аватара по email. */
function avaOf(email: string): [string, string] {
  let h = 0;
  for (let i = 0; i < email.length; i++) h = (h * 31 + email.charCodeAt(i)) >>> 0;
  return AVAS[h % AVAS.length];
}
const SEG_ICONS = { base: <House size={14} strokeWidth={2.2} />, away: <Bike size={14} strokeWidth={2.2} />, off: <Pause size={14} strokeWidth={2.2} /> };
const SEG_TITLES: Record<string, string> = {
  base: "На базе: отдать сейчас",
  away: "В пути: следующим заездом",
  off: "Не участвует в расчёте",
};

/** Русское склонение: plural(3, ["заказ", "заказа", "заказов"]) -> "заказа". */
function plural(n: number, forms: [string, string, string]) {
  const a = Math.abs(n) % 100, d = a % 10;
  if (a > 10 && a < 20) return forms[2];
  if (d > 1 && d < 5) return forms[1];
  if (d === 1) return forms[0];
  return forms[2];
}

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
  const [sheetTab, setSheetTab] = useState<"params" | "hist" | "prof" | "team">("prof");
  const [helpOpen, setHelpOpen] = useState(false);
  const [openAcc, setOpenAcc] = useState<"points" | "orders" | "couriers" | null>("orders");
  const [histDays, setHistDays] = useState("1");
  const [hist, setHist] = useState<HistData>(null);
  const [weekStats, setWeekStats] = useState<WeekStats | null>(null);
  const [courierName, setCourierName] = useState("");
  const [bindFor, setBindFor] = useState<Courier | null>(null); // привязка Telegram
  const [pwOld, setPwOld] = useState("");
  const [pwNew, setPwNew] = useState("");
  const [userEmail, setUserEmail] = useState("");
  const [userName, setUserName] = useState("");
  const [userPhone, setUserPhone] = useState("");
  const [profName, setProfName] = useState("");
  const [profPhone, setProfPhone] = useState("");
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
      setWeekStats(await api<WeekStats>("/api/stats/week"));
    } catch { setWeekStats(null); }
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
        <button className="iconbtn gear" title="Профиль" aria-label="Профиль"
          onClick={() => { setSheetTab("prof"); setSheetOpen(true); }}>
          <Settings size={16} /></button>
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
                    const foreign = (st.points || []).length > 1 && c.point_id !== st.my_point;
                    return (
                      <div key={c.id}
                        className={"ent crow" + (dragOverCourier === c.id ? " drop-hint" : "")}
                        draggable={!foreign}
                        title={foreign
                          ? `${c.name}: курьер другого депо — виден только для отслеживания`
                          : `${c.name}: перетащите в план развозки справа, чтобы включить в расчёт`}
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
                                  title={SEG_TITLES[s]} aria-label={"Статус: " + SEG_TITLES[s]}
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
                              ? <div className="c-row2 geo-row" title="В развозке: заказы у курьера, возврат — по живой геолокации">
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
                                    : <div className="c-row2 geo-row" title="Возврат рассчитан по живой геолокации курьера">
                                        <Timer size={11} /> вернётся ≈{c.geo.back_min} мин (по гео)
                                      </div>)
                           : foreign
                             ? <div className="c-row2 geo-row" title="Возврат считает диспетчер его точки">
                                 <Timer size={11} /> вернётся ≈{c.back_min ?? 15} мин
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
          weekStats={weekStats}
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
          userName={userName} userPhone={userPhone} setUserName={setUserName} setUserPhone={setUserPhone}
          profName={profName} profPhone={profPhone} setProfName={setProfName} setProfPhone={setProfPhone}
          onSyncProfile={() => { setProfName(st?.me?.name || ""); setProfPhone(st?.me?.phone || ""); }}
          onSaveProfile={async () => {
            try {
              setSt(await api<AppState>("/api/profile", "POST", { name: profName, phone: profPhone }));
              showToast("Профиль сохранён");
            } catch (e) { showToast((e as Error).message, true); }
          }}
          onAddUser={async () => {
            try {
              const s = await api<AppState>("/api/users", "POST", { email: userEmail, password: userPwd, is_admin: userIsAdmin, name: userName, phone: userPhone });
              setSt(s);
              setUserEmail(""); setUserPwd(""); setUserIsAdmin(false); setUserName(""); setUserPhone("");
              showToast("Пользователь добавлен");
            } catch (e) { showToast((e as Error).message, true); }
          }}
          onDelUser={async (uid, email) => {
            if (!(await askConfirm(`Удалить пользователя «${email}»?`, { ok: "Удалить", danger: true }))) return;
            try { setSt(await api<AppState>("/api/users/" + uid, "DELETE")); }
            catch (e) { showToast((e as Error).message, true); }
          }}
          onUpdUser={async (uid, data) => {
            try {
              setSt(await api<AppState>("/api/users/" + uid, "PUT", data));
              showToast("Изменения сохранены");
              return true;
            } catch (e) { showToast((e as Error).message, true); return false; }
          }}
          onResetPwd={async (uid, newPwd) => {
            try {
              await api("/api/users/" + uid + "/password", "PUT", { new: newPwd });
              showToast("Пароль обновлён");
              return true;
            } catch (e) { showToast((e as Error).message, true); return false; }
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
            <p className="note">План пересчитывается сам после изменений. Настройки, история и профиль — шестерёнка в шапке.</p>
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
  const [msg, setMsg] = useState("");
  const [sent, setSent] = useState(false);

  const send = async () => {
    setBusy(true); setErr("");
    try {
      const r = await api<{ ok?: boolean }>("/api/notify/tg", "POST",
        { chat_id: courier.tg_chat_id, text: msg.trim() });
      if (r.ok) { setMsg(""); setSent(true); setTimeout(onClose, 900); }
    } catch (e) {
      setErr(String((e as Error).message || e));
    } finally { setBusy(false); }
  };

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
        <button className="bind-close" aria-label="Закрыть" onClick={onClose}><X size={16} /></button>
        <div className="bind-head">
          <span className="bind-ico"><Send size={15} /></span>
          <div>
            <h3 style={{ margin: 0 }}>Telegram</h3>
            <span className="bind-sub">{courier.name}</span>
          </div>
          {courier.tg_chat_id
            ? <span className="bind-chip ok" title={`ID ${courier.tg_chat_id}`}><Check size={11} /> {courier.tg_login ? "@" + courier.tg_login : "ID " + courier.tg_chat_id}</span>
            : <span className="bind-chip">не привязан</span>}
        </div>

        {courier.tg_chat_id ? (<>
          <div className="bind-msg">
            <div className="bind-manual">
              <input placeholder={`Сообщение для ${courier.tg_login ? "@" + courier.tg_login : "ID " + courier.tg_chat_id}`}
                value={msg} onChange={e => setMsg(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter" && msg.trim() && !busy) void send(); }} />
              <button className="btn btn-primary" disabled={busy || !msg.trim()} onClick={() => void send()}>
                <Send size={13} /> Отправить
              </button>
            </div>
            <small className="bind-msg-note">{sent ? <><Check size={11} /> отправлено</> : "Сообщение придёт от имени бота в личный чат"}</small>
          </div>
          {err && <p className="bind-err" role="alert">{err}</p>}
          <button className="bind-unlink" disabled={busy} onClick={() => void unbind()}><Unlink size={13} /> Отвязать Telegram</button>
        </>) : (<>
          <div className="bind-steps">
            <span className="bind-step"><i>1</i><span>Курьер открывает бота{" "}
              {bot ? <a className="bind-bot" href={`https://t.me/${bot.replace(/^@/, "")}`}
                target="_blank" rel="noreferrer">{bot}</a> : <b>развозки</b>} и&nbsp;нажимает&nbsp;«Запустить»</span></span>
            <span className="bind-step"><i>2</i><span>Он появится в списке ниже — нажмите на него</span></span>
          </div>

          {seen.length > 0 && (
            <div className="bind-list">
              {seen.map(u => (
                <button key={u.chat_id} disabled={busy} className="bind-user"
                  title="Привязать этого пользователя к курьеру"
                  onClick={() => void bind(u.chat_id, u.login)}>
                  <span className="bind-user-l">
                    <b>@{u.login}</b>
                    <small>ID {u.chat_id} · {posAgeMin(u.ts) < 1 ? "только что" : posAgeMin(u.ts) + " мин назад"}</small>
                  </span>
                  <ArrowRight size={14} />
                </button>
              ))}
            </div>
          )}

          <div className="bind-divider">Курьер уже писал боту? Введите его ID</div>
          <div className="bind-manual">
            <input placeholder="ID из сообщения бота" value={manual} inputMode="numeric"
              onChange={e => setManual(e.target.value.replace(/\D/g, ""))} />
            <button className="btn btn-primary" disabled={busy || !manual} onClick={() => void bind(manual)}>Привязать</button>
          </div>
          {err && <p className="bind-err" role="alert">{err}</p>}
        </>)}
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
  const byRoads = plan.routing === "roads";

  return (
    <>
      <div className="plan-top">
        <div className="pt-label">Последняя доставка</div>
        <div className="pt-clock">≈{plan.last_delivery_clock || "?"} <small>+{plan.last_delivery_min} мин</small></div>
        <div className="pt-meta">
          {!byRoads && (
            <span className="pm-chip warn" title="Сервисы дорог (ORS/OSRM) недоступны — время и километры оценены по прямой, с запасом">
              <TriangleAlert size={11} /> расчёт по прямой
            </span>
          )}
          <span className="pm-chip" title="Время последнего расчёта плана">
            <Clock size={11} />
            рассчитано {(plan.solved_at || "").replace("T", " ").slice(11, 16)}
          </span>
          {plan.stale && (
            <span className="pm-chip warn" title="Данные менялись после расчёта">
              <RefreshCw size={11} /> устарел — нажмите «Рассчитать»
            </span>
          )}
          {plan.moved && (
            <span className="pm-chip" title="Порядок объезда правили перетаскиванием">
              <Hand size={11} /> правка вручную
            </span>
          )}
          {!!plan.unassigned && (
            <span className="pm-chip warn" title="Заказы, не поместившиеся ни в один маршрут (лимит заказов на курьера)">
              <TriangleAlert size={11} /> без маршрута: {plan.unassigned}
            </span>
          )}
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
              <span className="r-acts">
                {st.cfg?.tg && r.tg_chat_id && (
                  <button className="r-tg" title="Отправить маршрут курьеру в Telegram" onClick={() => onTg(r.courier_id)}><Send size={14} /></button>
                )}
                <button className="r-copy" title="Скопировать маршрут текстом, чтобы отправить курьеру" onClick={() => onCopy(r)}><ClipboardCopy size={14} /></button>
              </span>
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
                <span className="chip chip-amber" title="Курьер ещё в пути, маршрут сдвинут на время возврата">
                  старт +{r.start_delay_min} мин
                </span>
              )}
            </div>
            <div className="r-sub">
              <span title="Количество заказов в маршруте"><Package size={11} /> {r.count} {plural(r.count, ["заказ", "заказа", "заказов"])}</span>
              <span title="Ориентировочное время возврата на точку выдачи"><Timer size={11} /> вернётся ≈{clock(r.total_min)}</span>
              {r.distance_km ? <span title="Длина маршрута по дорогам"><RouteIcon size={11} /> {r.distance_km} км</span> : null}
              {r.speed_src && r.speed_src !== "default" && (
                <span title={r.speed_src === "geo"
                  ? "Замер по живой геолокации курьера — ETA пересчитаны под его скорость"
                  : "Оценка по темпу доставок относительно других курьеров — ETA пересчитаны под его скорость"}>
                  <Gauge size={11} /> ≈{r.speed_kmh} км/ч{r.speed_src === "geo" ? " (гео)" : " (темп)"}
                </span>
              )}
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
                            <span className="s-prio" title={`Приоритетный${s.auto ? ", поднялся сам по возрасту" : ""}`}><Zap size={11} /></span>
                          )} {s.address} {s.deadline && <span className="s-dl" title="Обещанное время доставки"><Timer size={11} />{s.deadline}</span>}
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
  const rel = a.chosen === "split" ? -delta : delta; // >0 — выбранный сценарий хуже второго
  const badge = rel === 0 ? null : (
    <span className={"adv-badge " + (rel < 0 ? "ok" : "no")}
      title="Разница выбранного сценария по времени последней доставки">
      {rel < 0 ? `−${Math.abs(rel)}` : `+${rel}`} мин
    </span>
  );
  const list = a.held.map(h => shortAddr(h.address));
  const row = (id: string, title: string, side: { counts: string; last_clock?: string; avg_min: number }) => (
    <button className={"adv-opt" + (a.chosen === id ? " chosen" : "")} title="Применить этот сценарий" onClick={() => onMode(id)}>
      <span className="adv-opt-t">
        <span className="adv-dot" aria-hidden="true" />
        {title}
        {a.chosen === id && <Check size={13} className="adv-done" />}
        {a.chosen === id && badge}
      </span>
      <span className="adv-opt-meta"><Users size={11} />{side.counts}</span>
      <span className="adv-opt-nums"><Clock size={11} />≈{side.last_clock || "?"} · ср {side.avg_min} мин</span>
    </button>
  );
  return (
    <div className={"advice" + (busy ? " busy" : "")} title="Сравнение сценариев: всё курьерам на базе сейчас или разделить с возвращающимся">
      <div className="adv-head">
        <Scale size={14} />
        <span className="adv-q">Ждать {nm}?</span>
        <span className="adv-back">{backTxt}</span>
      </div>
      <div className="adv-opts">
        {row("now", "Не ждать", a.now)}
        {row("split", "Ждать", a.split)}
      </div>
      {list.length > 0 && (
        <div className="adv-held" title={list.join("\n")}>
          <Paperclip size={11} />
          <span>{declName(a.held[0].courier)}: {list.slice(0, 3).join(" · ")}{list.length > 3 ? ` …ещё ${list.length - 3}` : ""}</span>
        </div>
      )}
    </div>
  );
}

/* ---------- «Ещё» ---------- */
const SET_SECTIONS: { id: string; title: string }[] = [
  { id: "move", title: "Время в пути" },
  { id: "addr", title: "У адреса" },
  { id: "trip", title: "Заезды и приоритет" },
];
const SET_FIELDS: { key: string; label: string; unit?: string; min: number; max: number; step?: number; sec: string; tip: string }[] = [
  { key: "speed_kmh", label: "Скорость", unit: "км/ч", min: 5, max: 120, sec: "move",
    tip: "Средняя скорость курьера между адресами. Это запасной расчёт на случай, когда дорожная матрица не ответила. В расчёте: время пути = расстояние ÷ эта скорость." },
  { key: "traffic", label: "Пробки", unit: "коэф.", min: 1, max: 3, step: 0.05, sec: "move",
    tip: "Общая надбавка к дорожному времени: 1 — свободно, 1.25 — обычный день, 1.5–2 — час пик. В расчёте: каждое время в пути из матрицы умножается на этот коэффициент." },
  { key: "lights_sec_per_km", label: "Светофоры", unit: "с/км", min: 0, max: 60, sec: "move",
    tip: "Средняя задержка на светофорах и перекрёстках. В расчёте: секунды добавляются к каждому километру пути; 15 с/км — это примерно +20–25% городского времени." },
  { key: "handover_min", label: "Вручение", unit: "мин", min: 0, max: 60, sec: "addr",
    tip: "Само вручение: позвонить, дождаться клиента, отдать заказ. В расчёте: добавляется к каждому адресу и сдвигает все последующие времена маршрута." },
  { key: "approach_center_min", label: "Подъезд: центр", unit: "мин", min: 0, max: 15, sec: "addr",
    tip: "Запас на парковку и путь до двери клиента для адресов ближе 2.5 км от точки выдачи. В расчёте: фиксированная добавка к каждому такому адресу." },
  { key: "approach_far_min", label: "Подъезд: окраины", unit: "мин", min: 0, max: 15, sec: "addr",
    tip: "То же для адресов дальше 2.5 км. Обычно меньше: на окраинах проще припарковаться. В расчёте: добавка к каждому дальнему адресу." },
  { key: "max_orders", label: "Заказов в заезде", unit: "шт", min: 1, max: 50, sec: "trip",
    tip: "Сколько заказов курьер уносит за один выезд — объём сумки. В расчёте: после этого числа курьер возвращается на точку, и начинается новый заезд." },
  { key: "reload_min", label: "Перезагрузка", unit: "мин", min: 0, max: 120, sec: "trip",
    tip: "Время на точке между заездами: сдать выполненное, принять новую партию, погрузиться. В расчёте: старт следующего заезда = финиш предыдущего + это время." },
  { key: "auto_prio_min", label: "Авто-приоритет", unit: "мин", min: 0, max: 240, sec: "trip",
    tip: "Заказ ждёт в очереди дольше этого времени — сам становится приоритетным. 0 — выключено. В расчёте: возраст заказа повышает его вес, решатель ставит его в маршрут раньше." },
];
const HOUR_TRAFFIC_TIP = "Пробки не постоянны: утром и вечером дороги медленнее, днём свободнее. В расчёте: коэффициент пробок берётся по часу выезда, а не один на весь день.";
const TIP_W = 290; // ширина .ptip-pop из globals.css

/** Строка «Права администратора» с тумблером — общая для добавления и редактирования диспетчера. */
function RoleSwitch({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="t-role">
      <span className="t-role-l">Права администратора
        <small>Параметры расчёта, участники и точки выдачи</small>
      </span>
      <button type="button" role="switch" aria-checked={on} aria-label="Права администратора"
        className={"pswitch" + (on ? " on" : "")}
        onClick={() => onChange(!on)}><i /></button>
    </div>
  );
}

function Sheet(p: {
  st: AppState;
  tab: "params" | "hist" | "prof" | "team";
  setTab: (t: "params" | "hist" | "prof" | "team") => void;
  hist: HistData;
  histDays: string;
  onHistDays: (d: string) => void;
  weekStats: WeekStats | null;
  pwOld: string; pwNew: string; setPwOld: (v: string) => void; setPwNew: (v: string) => void;
  onChangePw: () => Promise<void>;
  userEmail: string; userPwd: string; userIsAdmin: boolean;
  setUserEmail: (v: string) => void; setUserPwd: (v: string) => void; setUserIsAdmin: (v: boolean) => void;
  userName: string; userPhone: string; setUserName: (v: string) => void; setUserPhone: (v: string) => void;
  profName: string; profPhone: string; setProfName: (v: string) => void; setProfPhone: (v: string) => void;
  onSyncProfile: () => void; onSaveProfile: () => Promise<void>;
  onAddUser: () => Promise<void>;
  onDelUser: (uid: string, email: string) => Promise<void>;
  onUpdUser: (uid: string, data: { email: string; name: string; phone: string; is_admin: boolean }) => Promise<boolean>;
  onResetPwd: (uid: string, newPwd: string) => Promise<boolean>;
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
  const WD = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
  const _now0 = new Date();
  const wkCols = Array.from({ length: 7 }, (_, i) => {
    const dt = new Date(_now0.getFullYear(), _now0.getMonth(), _now0.getDate() - (6 - i));
    const day = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
    const rec = p.weekStats?.days.find(d => d.day === day);
    return { day, date: day, wd: WD[dt.getDay()], delivered: rec?.delivered || 0,
      cancelled: rec?.cancelled || 0, avg_cycle_min: rec?.avg_cycle_min ?? null };
  });
  const wkMax = Math.max(1, ...wkCols.map(d => d.delivered));
  const wkTotal = wkCols.reduce((a, d) => a + d.delivered, 0);
  const wkCour = p.weekStats?.couriers || [];
  useEffect(() => { if (p.tab === "prof") p.onSyncProfile(); }, [p.tab]); // eslint-disable-line react-hooks/exhaustive-deps
  const [profOpen, setProfOpen] = useState(true);
  const [addOpen, setAddOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [eMail, setEMail] = useState(""); const [eName, setEName] = useState("");
  const [ePhone, setEPhone] = useState(""); const [eAdmin, setEAdmin] = useState(false);
  const [ePwd, setEPwd] = useState("");
  const [pwOpen, setPwOpen] = useState(false);
  type TipState = { f: { key: string; label: string; tip: string }; left: number; top: number; below: boolean };
  const [tip, setTip] = useState<TipState | null>(null);
  const showTip = (f: TipState["f"], el: HTMLElement) => {
    const r = el.getBoundingClientRect();
    const below = r.top < 420; // над кнопкой места может не быть — показываем снизу
    setTip({
      f,
      left: Math.max(12, Math.min(r.left - 14, window.innerWidth - (TIP_W + 16))),
      top: below ? r.bottom + 7 : r.top - 8,
      below,
    });
  };
  const hideTip = () => setTip(null);
  const TITLES = { params: "Параметры расчёта", hist: "Статистика", prof: "Профиль", team: "Участники" } as const;
  return (
    <div className="sheet-bg" onClick={e => { if (e.target === e.currentTarget) p.onClose(); }}>
      <div className="sheet" aria-label={TITLES[p.tab]}>
        <button className="close" aria-label="Закрыть" onClick={p.onClose}>✕</button>
        <div className="sheet-tabs" role="tablist">
          <button role="tab" aria-selected={p.tab === "prof"} className={p.tab === "prof" ? "on" : ""}
            onClick={() => p.setTab("prof")}><User size={14} />Профиль</button>
          <button role="tab" aria-selected={p.tab === "hist"} className={p.tab === "hist" ? "on" : ""}
            onClick={() => p.setTab("hist")}><ChartColumn size={14} />Статистика</button>
          {!!p.st.me?.is_admin && (
            <button role="tab" aria-selected={p.tab === "params"} className={p.tab === "params" ? "on" : ""}
              onClick={() => p.setTab("params")}><SlidersHorizontal size={14} />Параметры расчёта</button>
          )}
          {!!p.st.me?.is_admin && (
            <button role="tab" aria-selected={p.tab === "team"} className={p.tab === "team" ? "on" : ""}
              onClick={() => p.setTab("team")}><Users size={14} />Участники</button>
          )}
        </div>

        {p.tab === "params" && !!p.st.me?.is_admin && (<>
          {SET_SECTIONS.map(sec => (
            <div className="psec" key={sec.id}>
              <h4>{sec.title}</h4>
              {SET_FIELDS.filter(f => f.sec === sec.id).map(f => (
                <div className="prow" key={f.key}>
                  <span className="plabel">{f.label}
                    <button type="button" className="ptip" aria-label={"Подсказка: " + f.label}
                      onMouseEnter={e => showTip(f, e.currentTarget)} onMouseLeave={hideTip}
                      onFocus={e => showTip(f, e.currentTarget)} onBlur={hideTip}
                      onClick={e => { e.preventDefault(); showTip(f, e.currentTarget); }}>
                      <CircleHelp size={14} /></button>
                  </span>
                  <span className="pval">
                    <span className="pfield">
                      <input type="number" min={f.min} max={f.max} step={f.step || 1}
                        defaultValue={s[f.key] as number}
                        key={f.key + String(s[f.key])}
                        onChange={e => void set({ [f.key]: +e.target.value })} />
                      <i className="punit">{f.unit}</i>
                    </span>
                  </span>
                </div>
              ))}
              {sec.id === "move" && (
                <div className="prow">
                  <span className="plabel">Почасовые пробки
                    <button type="button" className="ptip" aria-label="Подсказка: почасовые пробки"
                      onMouseEnter={e => showTip({ key: "hour_traffic", label: "Почасовые пробки", tip: HOUR_TRAFFIC_TIP }, e.currentTarget)} onMouseLeave={hideTip}
                      onFocus={e => showTip({ key: "hour_traffic", label: "Почасовые пробки", tip: HOUR_TRAFFIC_TIP }, e.currentTarget)} onBlur={hideTip}
                      onClick={e => { e.preventDefault(); showTip({ key: "hour_traffic", label: "Почасовые пробки", tip: HOUR_TRAFFIC_TIP }, e.currentTarget); }}>
                      <CircleHelp size={14} /></button>
                  </span>
                  <span className="pval">
                    <button type="button" role="switch" aria-checked={!!s.hour_traffic} aria-label="Почасовые пробки"
                      className={"pswitch" + (s.hour_traffic ? " on" : "")}
                      onClick={() => void set({ hour_traffic: s.hour_traffic ? 0 : 1 })}><i /></button>
                  </span>
                </div>
              )}
            </div>
          ))}
          {tip && (
            <div className={"ptip-pop" + (tip.below ? " below" : "")} role="tooltip" style={{ left: tip.left, top: tip.top }}>
              <b>{tip.f.label}.</b> {tip.f.tip}
            </div>
          )}
        </>)}

        {p.tab === "hist" && (<>
        <div className="stat-cards">
          <div className="scard"><small>Выдано</small><b>{summ.delivered || 0}</b></div>
          <div className="scard"><small>Отменено</small><b>{summ.cancelled || 0}</b></div>
          <div className="scard"><small>Средний цикл</small><b>{summ.avg_cycle_min != null ? summ.avg_cycle_min : "–"}{summ.avg_cycle_min != null && <i>мин</i>}</b></div>
          <div className="scard" title="Заказы, выданные не позже обещанного времени, за 7 дней">
            <small>Вовремя · 7 дней</small>
            <b>{p.weekStats?.on_time_total ? p.weekStats.on_time + " из " + p.weekStats.on_time_total : "–"}</b>
          </div>
        </div>

        <h4>Выдачи за 7 дней</h4>
        <div className="wk-chart" role="img" aria-label="Выдачи по дням за неделю">
          {wkCols.map(d => (
            <div className={"wk-col" + (d.delivered ? "" : " z")} key={d.day}
              title={`${d.date}: выдано ${d.delivered}${d.cancelled ? `, отменено ${d.cancelled}` : ""}${d.avg_cycle_min != null ? `, цикл ${d.avg_cycle_min} мин` : ""}`}>
              <span className="wk-num">{d.delivered || ""}</span>
              <span className="wk-bar"><i style={d.delivered ? { height: Math.max(8, Math.round(d.delivered / wkMax * 100)) + "%" } : undefined} /></span>
              <span className="wk-day">{d.wd}</span>
              <span className="wk-date">{d.date.slice(8, 10)}.{d.date.slice(5, 7)}</span>
            </div>
          ))}
        </div>
        {wkTotal === 0 && <div className="empty-list">На этой неделе пока нет закрытых заказов</div>}

        {wkCour.length > 0 && (<>
        <h4>Курьеры за неделю</h4>
        <div className="wk-cour">
          {wkCour.map(c => (
            <div className="wk-cour-row" key={c.courier}>
              <Bike size={14} />
              <span className="wk-cour-name">{c.courier}</span>
              <span className="wk-cour-n">{c.delivered} выдано</span>
              {c.avg_cycle_min != null && <span className="wk-cour-cyc">цикл {c.avg_cycle_min} мин</span>}
            </div>
          ))}
        </div>
        </>)}

        <h4>История заказов</h4>
        <div className="hist-bar">
          <div className="pseg" role="group" aria-label="Период истории">
            {[["1", "Сегодня"], ["2", "2 дня"], ["7", "7 дней"], ["31", "31 день"]].map(([v, label]) => (
              <button type="button" key={v} className={p.histDays === v ? "on" : ""}
                onClick={() => p.onHistDays(v)}>{label}</button>
            ))}
          </div>
          <span className="hist-links">
            <a href="/report/day" target="_blank" rel="noopener" className="btn btn-primary"
              title="Отчёт дня для печати: Ctrl+P позволяет сохранить в PDF"><FileText size={14} />PDF</a>
            <a href={"/api/history/export?days=" + p.histDays} className="btn"
              title="Выгрузить историю в CSV (открывается в Excel)"><FileSpreadsheet size={14} />Excel</a>
          </span>
        </div>
        <div className="hist-list">
          {p.hist?.rows?.length ? (<>
          <div className="hrow hhead">
            <span>Время</span><span>Адрес</span><span>Курьер</span><span>Цикл</span>
          </div>
          {p.hist.rows.map((r, i) => (
            <div className={"hrow " + (r.outcome === "delivered" ? "ok" : "no")} key={i}>
              <span className="h-time"><b>{(r.closed_at || "").slice(11, 16)}</b><small>{(r.closed_at || "").slice(8, 10)}.{(r.closed_at || "").slice(5, 7)}</small></span>
              <span className="h-addr" title={r.address}>{r.address || ""}</span>
              <span className="h-cour">{r.courier || "–"}</span>
              <span className="h-res"><span className="h-badge">{r.outcome === "delivered" ? "✓" : "✕"}</span>{r.cycle_min != null ? <span className="h-cyc">{r.cycle_min + " мин"}</span> : null}</span>
            </div>
          ))}
          </>)
            : <div className="empty-list">Пока пусто</div>}
        </div>
        </>)}

        {p.tab === "prof" && (<>
        <div className={"sacc" + (profOpen ? " open" : "")}>
          <button type="button" className="sacc-h" aria-expanded={profOpen}
            onClick={() => setProfOpen(v => !v)}>Подпись курьерам<ChevronDown size={16} className="chev" /></button>
          <div className="sacc-b"><div className={"sacc-c col prof-col" + (p.st.me && !(p.st.me.name && p.st.me.phone) ? " need" : "")}>
            <label className="pf-note">Имя и телефон автоматически добавляются под каждым сообщением</label>
            <div className="prof-grid">
              <label className="pf-l">Имя
                <input type="text" placeholder="Настя" aria-label="Ваше имя" value={p.profName}
                  onChange={e => p.setProfName(e.target.value)} /></label>
              <label className="pf-l">Телефон
                <input type="tel" placeholder="+375 29 123-45-67" aria-label="Ваш телефон" value={p.profPhone}
                  inputMode="tel" onChange={e => p.setProfPhone(e.target.value)} /></label>
            </div>
            <div className="prof-sign" aria-live="polite">
              <small>Так придёт курьеру:</small>
              <div className="prof-sign-bubble">
                Есть вопросы? - {p.profName.trim() || <i>имя</i>}, {p.profPhone.trim() || <i>телефон</i>}
              </div>
            </div>
            <div className="prof-actions">
              <button className="btn btn-primary" disabled={!(p.profName.trim() && p.profPhone.trim())}
                onClick={() => void p.onSaveProfile()}>Сохранить</button>
              {p.st.me && !(p.st.me.name && p.st.me.phone) && (
                <small className="prof-warn">Пока без подписи — отправка сообщений курьерам недоступна</small>
              )}
             </div>
          </div></div>
        </div>

        <div className={"sacc" + (pwOpen ? " open" : "")}>
          <button type="button" className="sacc-h" aria-expanded={pwOpen}
            onClick={() => setPwOpen(v => !v)}>Смена пароля<ChevronDown size={16} className="chev" /></button>
          <div className="sacc-b"><div className="sacc-c col">
            <div className="prof-grid">
              <label className="pf-l">Старый пароль
                <input type="password" placeholder="••••" aria-label="Старый пароль"
                  value={p.pwOld} onChange={e => p.setPwOld(e.target.value)} /></label>
              <label className="pf-l">Новый пароль
                <input type="password" placeholder="минимум 4 символа" aria-label="Новый пароль"
                  value={p.pwNew} onChange={e => p.setPwNew(e.target.value)} /></label>
            </div>
            <button className="btn" disabled={!(p.pwOld && p.pwNew.length >= 4)}
              onClick={() => void p.onChangePw()}>Сменить пароль</button>
          </div></div>
        </div>
        </>)}

        {p.tab === "team" && !!p.st.me?.is_admin && (<>
          <div className="psec">
            <h4>Диспетчеры · {(p.st.users || []).length}</h4>
            {(p.st.users || []).map(u => {
              const [abg, afg] = avaOf(u.email);
              const ini = initialsOf(u.name || "", u.email);
              const isSelf = u.id === p.st.me?.id;
              const editing = editId === u.id;
              const startEdit = () => { setEditId(u.id); setEMail(u.email); setEName(u.name || ""); setEPhone(u.phone || ""); setEAdmin(!!u.is_admin); setEPwd(""); };
              return (
                <div key={u.id}>
                  <div className="trow">
                    <span className="t-ava" aria-hidden="true" style={{ background: abg, color: afg }}>{ini}</span>
                    <span className="t-who">
                      <b>{u.name || u.email.split("@")[0]}</b>
                      <small>{u.email}{u.phone ? " · " + u.phone : ""}</small>
                    </span>
                    {!!u.is_admin && <span className="chip chip-green">админ</span>}
                    {isSelf && <span className="chip">это вы</span>}
                    <button type="button" className={"ticon" + (editing ? " on" : "")} title="Изменить"
                      aria-label={"Изменить " + u.email} aria-expanded={editing}
                      onClick={() => (editing ? setEditId(null) : startEdit())}><Pencil size={15} /></button>
                    <button type="button" className="tdel" disabled={isSelf}
                      title={isSelf ? "Себя удалить нельзя" : "Удалить пользователя"}
                      aria-label={"Удалить " + u.email}
                      onClick={() => void p.onDelUser(u.id, u.email)}><Trash2 size={15} /></button>
                  </div>
                  {editing && (
                    <div className="tedit">
                      <div className="team-grid">
                        <label className="pf-l">Имя
                          <input type="text" aria-label="Имя пользователя" value={eName}
                            onChange={e => setEName(e.target.value)} /></label>
                        <label className="pf-l">Телефон
                          <input type="tel" placeholder="+375 29 123-45-67" aria-label="Телефон пользователя"
                            inputMode="tel" value={ePhone} onChange={e => setEPhone(e.target.value)} /></label>
                        <label className="pf-l">Email
                          <input type="email" aria-label="Email пользователя" value={eMail}
                            onChange={e => setEMail(e.target.value)} /></label>
                        <label className="pf-l">Новый пароль
                          <input type="password" placeholder="оставьте пустым, чтобы не менять"
                            aria-label="Новый пароль пользователя" value={ePwd}
                            onChange={e => setEPwd(e.target.value)} /></label>
                      </div>
                      {!isSelf && <RoleSwitch on={eAdmin} onChange={setEAdmin} />}
                      <div className="tedit-actions">
                        <button className="btn btn-primary"
                          disabled={!(eMail.trim() && eName.trim().length >= 2 && ePhone.trim())}
                          onClick={async () => {
                            const ok = await p.onUpdUser(u.id, { email: eMail.trim(), name: eName.trim(), phone: ePhone.trim(), is_admin: isSelf ? true : eAdmin });
                            const pwdOk = !ePwd || await p.onResetPwd(u.id, ePwd);
                            if (ok && pwdOk) setEditId(null);
                          }}>Сохранить</button>
                        <button className="btn" onClick={() => setEditId(null)}>Отмена</button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          <div className={"sacc" + (addOpen ? " open" : "")} style={{ marginTop: 12 }}>
            <button type="button" className="sacc-h" aria-expanded={addOpen}
              onClick={() => setAddOpen(v => !v)}>Добавить диспетчера<ChevronDown size={16} className="chev" /></button>
            <div className="sacc-b"><div className="sacc-c col team-col">
              <div className="team-grid">
                <label className="pf-l">Имя
                  <input type="text" placeholder="Иван" aria-label="Имя нового пользователя"
                    value={p.userName} onChange={e => p.setUserName(e.target.value)} /></label>
                <label className="pf-l">Телефон
                  <input type="tel" placeholder="+375 29 123-45-67" aria-label="Телефон нового пользователя"
                    inputMode="tel" value={p.userPhone} onChange={e => p.setUserPhone(e.target.value)} /></label>
                <label className="pf-l">Email
                  <input type="email" placeholder="ivan@cafe.by" aria-label="Email нового пользователя"
                    value={p.userEmail} onChange={e => p.setUserEmail(e.target.value)} /></label>
                <label className="pf-l">Начальный пароль
                  <input type="password" placeholder="минимум 4 символа" aria-label="Начальный пароль"
                    value={p.userPwd} onChange={e => p.setUserPwd(e.target.value)} /></label>
              </div>
              <RoleSwitch on={p.userIsAdmin} onChange={p.setUserIsAdmin} />
              <button className="btn btn-primary" disabled={!(p.userEmail.trim() && p.userPwd.length >= 4)}
                onClick={() => void p.onAddUser()}>Добавить</button>
            </div></div>
          </div>

          <a href="/api/backup" className="t-back" title="Скачать снимок базы данных">
            <Download size={14} />Бэкап базы
          </a>
        </>)}
      </div>
    </div>
  );
}

/* адрес депо: подпись из GeoInput или ручной текст */
function orderLabelDepot(p: GeoItem) { return p.label; }
