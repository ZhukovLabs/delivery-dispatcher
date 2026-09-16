export interface Depot { address: string; lat: number; lng: number; }
export interface PickPoint { id: string; name: string; address: string; lat: number; lng: number; couriers?: number; admins?: string[]; }
export interface CourierPos { lat: number; lng: number; ts: number; live?: boolean; acc?: number; }
export interface CourierGeo { lat: number; lng: number; age_min: number; back_min: number; at_depot: boolean; live?: boolean; at_order?: string; has_out?: boolean; loaded?: boolean; delivering?: boolean; to_point_min?: number; }
/* остановка активной развозки: [lat, lng] или [lat, lng, order_id] */
export type OutStop = [number, number] | [number, number, string];
export interface Courier {
  id: string; name: string; status?: "base" | "away" | "off";
  color?: string; back_min?: number; tg_chat_id?: string; tg_login?: string;
  pos?: CourierPos; geo?: CourierGeo; point_id?: string;
  cur_kmh?: number; avg_kmh?: number; speed_src?: "geo" | "delivery" | "default";
  out_route?: { stops: OutStop[]; home?: { lat: number; lng: number } | null; geom?: [number, number][] };
}
export interface Order {
  id: string; address: string; lat: number; lng: number;
  prio?: boolean; deadline?: string; created_at: string;
  status?: "ready" | "out"; assigned?: string; point_id?: string; pin?: string;
}
export interface Stop {
  order_id: string; address: string; eta_min: number; eta_clock?: string;
  lat: number; lng: number; prio?: boolean; auto?: boolean;
  deadline?: string; late_min?: number;
}
export interface Trip {
  stops: Stop[]; total_min: number; start_delay_min: number;
  start_clock?: string; end_clock?: string; distance_km?: number;
  geometry?: [number, number][];
}
export interface Route {
  courier_id: string; courier_name: string; status: string; color: string;
  stops: Stop[]; trips: Trip[]; count: number; total_min: number;
  start_delay_min: number; distance_km?: number; tg_chat_id?: string;
  home_point?: PickPoint;
  speed_kmh?: number; speed_src?: "geo" | "delivery" | "default";
}
export interface AdviceSide { counts: string; last_clock?: string; avg_min: number; }
export interface Advice {
  chosen: string; recommend?: string;
  wait_couriers: { name: string; back_min: number; back_clock?: string }[];
  now: AdviceSide; split: AdviceSide;
  gain_last_min: number; gain_avg_min?: number;
  held: { courier: string; address: string }[];
}
export interface Plan {
  routes: Route[]; last_delivery_clock?: string; last_delivery_min: number;
  avg_delivery_min?: number; solved_at: string; routing?: string;
  provider?: string; stale?: boolean; moved?: boolean; unassigned?: number;
  advice?: Advice | null;
}
export interface AppState {
  rev?: number; // версия состояния (long-poll /api/rev)
  depot: Depot | null; points?: PickPoint[]; couriers: Courier[]; orders: Order[];
  my_point?: string;
  settings: Record<string, number>;
  plan: Plan | null; me?: { id: string; email: string; is_admin?: number; name?: string; phone?: string };
  users?: { id: string; email: string; is_admin?: number; name?: string; phone?: string }[];
  today?: { delivered?: number; cancelled?: number; avg_cycle_min?: number };
  ors?: { used?: number; soft_limit?: number; paused?: boolean };
  cfg?: { tg?: boolean };
  tg?: { bot?: string; seen?: { chat_id: string; login: string; ts: number }[] };
  events?: { t: number; actor: string; text: string }[]; // лента активности
  solving?: boolean; // в этом депо идёт расчёт развозки (блокирует UI)
}

/** Сетевой таймаут для запросов: без него fetch на рваной связи висит
 *  до браузерного лимита (~300 с) и пользователь смотрит в «ничего». */
const API_TIMEOUT_MS = 30_000;

/** База REST API: в проде ходим НАПРЯМУЮ с API-хостом (funnel), мимо
 *  реврайтов Vercel — каждый клик диспетчера экономит ~90 мс хопа.
 *  В dev — same-origin через next rewrites (пустая строка). */
export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ||
  (typeof window !== "undefined" && !/^(localhost|127\.)/.test(window.location.hostname)
    ? "https://zhukovlabs.taila8c324.ts.net:8443"
    : "");

/** Сырой fetch к API с базой и cookie: для мест, где нужен сам Response
 *  (login проверяет статус, ws-token не должен редиректить на 401). */
export function fetchApi(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(API_URL + path, { ...init, credentials: "include" });
}

export class NetworkError extends Error {
  constructor() { super("Сеть недоступна — проверьте подключение к интернету"); }
}
export const isNetworkError = (e: unknown): e is NetworkError => e instanceof NetworkError;
const networkError = () => new NetworkError();

export async function api<T = AppState>(path: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
  const ctl = new AbortController();
  const timeout = setTimeout(() => ctl.abort(), API_TIMEOUT_MS);
  const onOuterAbort = () => ctl.abort(); // внешний сигнал тоже должен рвать запрос
  signal?.addEventListener("abort", onOuterAbort);
  let res: Response;
  try {
    res = await fetchApi(path, {
      method,
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: ctl.signal,
    });
  } catch {
    // обрыв связи / таймаут: единая понятная ошибка; при восстановлении WS
    // состояние тихо пересинхронизируется (invalidateQueries on connect)
    throw networkError();
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", onOuterAbort);
  }
  if (res.status === 401 && typeof window !== "undefined") {
    window.location.href = "/login";
    throw new Error("Требуется вход");
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    // 5xx без нашего JSON-поля error — это прокси за недоступным бэкендом
    // (dev-реврайт, Vercel, funnel), а не ответ приложения
    if (!data?.error && res.status >= 500) throw networkError();
    throw new Error((data as { error?: string } | null)?.error || res.statusText);
  }
  return data as T;
}

export const fmtCoords = (ll?: { lat?: number; lng?: number } | null) =>
  ll && ll.lat != null && ll.lng != null ? ll.lat.toFixed(5) + ", " + ll.lng.toFixed(5) : "";

/** Возраст метки времени: «25 с», «3 мин», «2 ч 5 мин» (секунды — эпоха). */
export const fmtAge = (ts: number) => {
  const s = Math.max(0, Math.round((Date.now() - ts * 1000) / 1000));
  if (s < 60) return `${s} с`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} мин`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h} ч ${m % 60} мин` : `${h} ч`;
};
