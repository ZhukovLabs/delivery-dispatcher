export interface Depot { address: string; lat: number; lng: number; }
export interface PickPoint { id: string; name: string; address: string; lat: number; lng: number; couriers?: number; admins?: string[]; }
export interface CourierPos { lat: number; lng: number; ts: number; live?: boolean; acc?: number; }
export interface CourierGeo { lat: number; lng: number; age_min: number; back_min: number; at_depot: boolean; live?: boolean; at_order?: string; has_out?: boolean; loaded?: boolean; delivering?: boolean; to_point_min?: number; }
export interface Courier {
  id: string; name: string; status: "base" | "away" | "off";
  color?: string; back_min?: number; tg_chat_id?: string; tg_login?: string;
  pos?: CourierPos; geo?: CourierGeo; point_id?: string;
  cur_kmh?: number; avg_kmh?: number; speed_src?: "geo" | "delivery" | "default";
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
}

export async function api<T = AppState>(path: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal,
  });
  if (res.status === 401 && typeof window !== "undefined") {
    window.location.href = "/login";
    throw new Error("Требуется вход");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data as { error?: string }).error || res.statusText);
  return data as T;
}

export const fmtCoords = (ll?: { lat: number; lng: number } | null) =>
  ll ? ll.lat.toFixed(5) + ", " + ll.lng.toFixed(5) : "";
