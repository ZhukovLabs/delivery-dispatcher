// Чистые гео/формат-хелперы карты: без React и без состояния.
import type { AppState, Plan } from "@/lib/api";

// = TG_GEO_AT_PLACE бэкенда: радиус, внутри которого курьеру зачтётся
// простой «у адреса» (30 с — и бот спросит «доставлен?»)
export const GEO_AT_PLACE_M = 150;

export const SCOOTER_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="5.5" cy="17.2" r="2.1"/><circle cx="18.6" cy="17.2" r="2.1"/><path d="M7.6 17.2h6.3l1-7.4h1.7"/><path d="M14.9 9.8h2.1l1.7 7.4"/><path d="M4.6 8.2h3.4l.9 3.6"/></svg>`;
/* склад: депо — самый заметный маркер, всегда поверх курьеров */
export const WAREHOUSE_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 9.6 12 4l9.5 5.6V20a1 1 0 0 1-1 1h-17a1 1 0 0 1-1-1Z"/><path d="M6.5 21v-6.5h4V21"/><path d="M13.5 21v-6.5h4V21"/><path d="M9 9.4h6"/></svg>`;

export const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

export const ANIM_MS = 2400; // плавный «догон» курьера до свежей геопозиции

export const DEFAULT_COURIER_COLOR = "#e8482b"; // курьер без назначенного цвета
export const ORDER_GRAY = "#8b95a8";            // заказ вне плана и без исполнителя
export const DUP_RED = "#d92d20";               // дублирующийся адрес

export const havKm = (a: [number, number], b: [number, number]) => {
  const r = Math.PI / 180;
  const h = Math.sin((b[0] - a[0]) * r / 2) ** 2 +
    Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin((b[1] - a[1]) * r / 2) ** 2;
  return 6371 * 2 * Math.asin(Math.sqrt(h));
};

export const clockIn = (min: number) =>
  new Date(Date.now() + Math.max(0, min) * 60000).toTimeString().slice(0, 5);

// опоздание против дедлайна «ЧЧ:ММ» по ETA (мин) — или null, если успевает
export const lateByDeadline = (deadline: string | undefined, etaMin: number): number | null => {
  const m = /^(\d{1,2}):(\d{2})$/.exec(deadline || "");
  if (!m) return null;
  const dl = +m[1] * 60 + +m[2];
  const now = new Date();
  const arr = now.getHours() * 60 + now.getMinutes() + etaMin;
  return arr > dl ? Math.round(arr - dl) : null;
};

/* маршрут курьера из плана (если есть) */
export const routeOf = (p: Plan | null | undefined, cid: string) =>
  p?.routes.find(r => r.courier_id === cid);

/* индекс ближайшей точки полилинии к p — для обрезки маршрута */
const nearestIdx = (pl: [number, number][], p: [number, number]) => {
  let bi = 0, bd = Infinity;
  for (let i = 0; i < pl.length; i++) {
    const dx = pl[i][0] - p[0], dy = pl[i][1] - p[1], d = dx * dx + dy * dy;
    if (d < bd) { bd = d; bi = i; }
  }
  return bi;
};
export const trimAfter = (pl: [number, number][], p: [number, number]) =>
  pl.slice(0, nearestIdx(pl, p) + 1);          // без хвоста после точки p

export const OFF_ROUTE = 0.0022;               // ~250 м: дальше — «сошёл с маршрута»
/* ближайшая точка полилинии не раньше индекса start (курьер едет вперёд —
   линию за ним подрезаем только вперёд, без откатов); -1 — точка мимо линии */
export const nearestIdxFrom = (pl: [number, number][], p: [number, number], start: number) => {
  let bi = -1, bd = OFF_ROUTE * OFF_ROUTE;
  for (let i = Math.max(0, start); i < pl.length; i++) {
    const dx = pl[i][0] - p[0], dy = pl[i][1] - p[1], d = dx * dx + dy * dy;
    if (d < bd) { bd = d; bi = i; }
  }
  return bi;
};

/* линия от ближайшей к курьеру точки маршрута до его конца: даже если
   курьер чуть в стороне от нарисованной дороги (другая версия данных
   роутера), оставшийся путь рисуем от ближайшей точки — не гася линию */
export const trimFrom = (pl: [number, number][], p: [number, number]): [number, number][] => {
  let bi = 0, bd = Infinity;
  for (let i = 0; i < pl.length; i++) {
    const dx = pl[i][0] - p[0], dy = pl[i][1] - p[1], d = dx * dx + dy * dy;
    if (d < bd) { bd = d; bi = i; }
  }
  return pl.slice(bi);
};

/* координаты трипа: дорожная геометрия (без возврата на базу) или
   прямая через остановки — тоже только до последней остановки */
export const tripCoords = (tr: { geometry?: [number, number][]; stops: { lat: number; lng: number }[] },
                           depot: [number, number]): [number, number][] => {
  const stops = tr.stops.map(s => [s.lat, s.lng] as [number, number]);
  return tr.geometry && tr.geometry.length > 1
    ? trimAfter(tr.geometry, stops[stops.length - 1] || depot)
    : [depot, ...stops];
};

/* общий кадр: все заказы + точки выдачи с запасом */
export function fitAll(ym: any, map: any, s: AppState) {
  const pts: [number, number][] = s.orders.map(o => [o.lat, o.lng] as [number, number]);
  if (s.points?.length) s.points.forEach(p => pts.push([p.lat, p.lng] as [number, number]));
  else if (s.depot) pts.push([s.depot.lat, s.depot.lng]);
  if (!pts.length) return;
  const lats = pts.map(p => p[0]), lngs = pts.map(p => p[1]);
  const padLat = Math.max((Math.max(...lats) - Math.min(...lats)) * 0.18, 0.004);
  const padLng = Math.max((Math.max(...lngs) - Math.min(...lngs)) * 0.18, 0.004);
  map.setBounds(
    [[Math.min(...lats) - padLat, Math.min(...lngs) - padLng], [Math.max(...lats) + padLat, Math.max(...lngs) + padLng]],
    { checkZoomRange: true },
  );
}
