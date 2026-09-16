"use client";

import { useEffect, useRef, useState } from "react";
import { fmtAge, type AppState, type Plan, type Courier, type Order } from "@/lib/api";

// = TG_GEO_AT_PLACE бэкенда: радиус, в котором курьеру зачтётся простой
// «у адреса» (после 30 с — вопрос «доставлен?» в TG)
const GEO_AT_PLACE_M = 150;

const havKm = (a: [number, number], b: [number, number]) => {
  const r = Math.PI / 180;
  const h = Math.sin((b[0] - a[0]) * r / 2) ** 2 +
    Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin((b[1] - a[1]) * r / 2) ** 2;
  return 6371 * 2 * Math.asin(Math.sqrt(h));
};

const clockIn = (min: number) =>
  new Date(Date.now() + Math.max(0, min) * 60000).toTimeString().slice(0, 5);

// опоздание против дедлайна «ЧЧ:ММ» по ETA (мин) — или null, если успевает
const lateByDeadline = (deadline: string | undefined, etaMin: number): number | null => {
  const m = /^(\d{1,2}):(\d{2})$/.exec(deadline || "");
  if (!m) return null;
  const dl = +m[1] * 60 + +m[2];
  const now = new Date();
  const arr = now.getHours() * 60 + now.getMinutes() + etaMin;
  return arr > dl ? Math.round(arr - dl) : null;
};

declare global {
  interface Window { ymaps?: any; }
}

export interface MapViewProps {
  state: AppState;
  pickMode: boolean;
  onPick: (ll: { lat: number; lng: number }) => void;
  fitSignal: number;
  hoverOid: string | null;
  onMarkerClick: (oid: string) => void;
  dupOids?: Set<string>;
  focus?: { kind: "order" | "courier" | "point"; id: string; n: number } | null;
  pickPreview?: { lat: number; lng: number } | null;
}

/* ---------- загрузка api-maps один раз на страницу ---------- */

let ymapsPromise: Promise<any> | null = null;
function loadYmaps(): Promise<any> {
  if (!ymapsPromise) {
    ymapsPromise = new Promise((resolve, reject) => {
      const w = window as any;
      if (w.ymaps) { w.ymaps.ready(() => resolve(w.ymaps)); return; }
      const s = document.createElement("script");
      s.src = "https://api-maps.yandex.ru/2.1/?lang=ru_RU" +
        (process.env.NEXT_PUBLIC_YMAPS_KEY ? `&apikey=${process.env.NEXT_PUBLIC_YMAPS_KEY}` : "");
      s.async = true;
      s.onload = () => w.ymaps.ready(() => resolve(w.ymaps));
      s.onerror = () => { ymapsPromise = null; reject(new Error("не удалось загрузить Яндекс.Карты")); };
      document.head.appendChild(s);
    });
  }
  return ymapsPromise;
}

const SCOOTER_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="5.5" cy="17.2" r="2.1"/><circle cx="18.6" cy="17.2" r="2.1"/><path d="M7.6 17.2h6.3l1-7.4h1.7"/><path d="M14.9 9.8h2.1l1.7 7.4"/><path d="M4.6 8.2h3.4l.9 3.6"/></svg>`;
/* склад: депо — самый заметный маркер, всегда поверх курьеров */
const WAREHOUSE_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 9.6 12 4l9.5 5.6V20a1 1 0 0 1-1 1h-17a1 1 0 0 1-1-1Z"/><path d="M6.5 21v-6.5h4V21"/><path d="M13.5 21v-6.5h4V21"/><path d="M9 9.4h6"/></svg>`;

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const ANIM_MS = 2400; // плавный «догон» курьера до свежей геопозиции

const DEFAULT_COURIER_COLOR = "#e8482b"; // курьер без назначенного цвета
const ORDER_GRAY = "#8b95a8";            // заказ вне плана и без исполнителя
const DUP_RED = "#d92d20";               // дублирующийся адрес

/* маршрут курьера из плана (если есть) */
const routeOf = (p: Plan | null | undefined, cid: string) =>
  p?.routes.find(r => r.courier_id === cid);

/* координаты трипа: дорожная геометрия или прямая через остановки */
const tripCoords = (tr: { geometry?: [number, number][]; stops: { lat: number; lng: number }[] },
                    depot: [number, number]): [number, number][] =>
  tr.geometry && tr.geometry.length > 1
    ? tr.geometry
    : [depot, ...tr.stops.map(s => [s.lat, s.lng] as [number, number]), depot];

export default function MapView({ state, pickMode, onPick, fitSignal, hoverOid, onMarkerClick, dupOids, focus, pickPreview }: MapViewProps) {
  const divRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<any>(null);
  const ymRef = useRef<any>(null);
  const [ready, setReady] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // зеркало выбора в стейт: клик по курьеру мгновенно перекрашивает его точки
  const [selTick, setSelTick] = useState(0);

  // свежие колбэки для обработчиков карты без пересоздания карты
  const pickRef = useRef(pickMode);
  const onPickRef = useRef(onPick);
  const onMarkerClickRef = useRef(onMarkerClick);
  pickRef.current = pickMode;
  onPickRef.current = onPick;
  onMarkerClickRef.current = onMarkerClick;

  // инкрементальные слои: маркеры не пересоздаются на каждом обновлении
  const L = useRef({
    depots: new Map<string, any>(),
    orders: new Map<string, any>(),
    couriers: new Map<string, any>(),
    orderSig: new Map<string, string>(),   // сигнатуры вида маркера
    courierSig: new Map<string, string>(),
    lines: [] as any[],
    routeLine: null as any | null,
    routeSig: "",
  });
  const anims = useRef(new Map<string, { pm: any; from: [number, number]; to: [number, number]; start: number }>());
  const rafRef = useRef(0);
  const selCid = useRef<string | null>(null); // выбранный курьер: подсветка его маршрута
  const didInitialFit = useRef(false);
  const lastSignal = useRef(fitSignal);
  const hoverRef = useRef<string | null>(null);
  const lastMyPoint = useRef<string | null>(null);

  /* ---------- инициализация карты ---------- */
  useEffect(() => {
    let dead = false;
    loadYmaps().then(ym => {
      if (dead || !divRef.current || mapRef.current) return;
      ymRef.current = ym;
      const map = new ym.Map(divRef.current, {
        center: [52.4345, 31.0137],
        zoom: 13,
        controls: ["zoomControl", "fullscreenControl", "geolocationControl"],
      }, { suppressMapOpenBlock: true, yandexMapDisablePoiInteractivity: true });
      mapRef.current = map;
      // ховер-балуны не должны дёргать карту — подтягиваемся только по клику (#8)
      map.options.set("balloonAutoPan", false);
      map.events.add("click", (e: any) => {
        if (pickRef.current) {
          const c = e.get("coords") as [number, number];
          onPickRef.current({ lat: c[0], lng: c[1] });
          return;
        }
        // клик мимо маркеров — снять выбор курьера и убрать его маршрут
        if (selCid.current) {
          selCid.current = null;
          refreshRef.current();
          setSelTick(t => t + 1);
        }
      });
      map.container.fitToViewport();
      setReady(true);
    }).catch(() => !dead && setErr("Карта не загрузилась — проверьте интернет"));
    return () => {
      dead = true;
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      if (mapRef.current) { mapRef.current.destroy(); mapRef.current = null; }
    };
  }, []);

  useEffect(() => {
    const el = divRef.current;
    if (el) el.classList.toggle("pick-cross", !!pickMode);
    const t = setTimeout(() => mapRef.current?.container.fitToViewport(), 120);
    return () => clearTimeout(t);
  }, [pickMode, ready]);

  /* ---------- плавное движение курьеров (rAF-интерполяция) ---------- */
  const ensureRaf = () => {
    if (rafRef.current) return;
    const step = () => {
      const t0 = performance.now();
      let alive = false;
      anims.current.forEach((a, cid) => {
        const t = Math.min(1, (t0 - a.start) / ANIM_MS);
        const e = t < .5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
        a.pm.geometry.setCoordinates([a.from[0] + (a.to[0] - a.from[0]) * e,
                                      a.from[1] + (a.to[1] - a.from[1]) * e]);
        if (t < 1) alive = true; else anims.current.delete(cid);
      });
      rafRef.current = alive ? requestAnimationFrame(step) : 0;
    };
    rafRef.current = requestAnimationFrame(step);
  };

  const flyTo = (coords: [number, number]) => {
    const map = mapRef.current;
    if (!map) return;
    if (map.getZoom() < 15) map.setZoom(15, { smooth: true, duration: 200 });
    map.panTo(coords, { flying: true, delay: 0, duration: 420 });
  };

  /* ---------- содержимое: маркеры и линии ---------- */
  const d = state.depot;
  const pickPts = state.points?.length ? state.points
    : d ? [{ id: "", name: "Основная", address: d.address, lat: d.lat, lng: d.lng }] : [];
  /* содержимое балуна заказа: адрес → курьер → ETA → радиус простоя →
     приоритет → дедлайн → опоздание (см. требование к точке доставки) */
  const popupHtml = (p: { address: string; courier?: string; eta?: string;
                          lateMin?: number; prio?: boolean; deadline?: string;
                          note?: string }) =>
    `<b>${esc(p.address)}</b>` +
    (p.courier ? `<br>🛍 ${esc(p.courier)}` : "") +
    (p.eta ? `<br>≈${esc(p.eta)}${p.lateMin ? ` · <span style="color:#b3261e">опоздание ~${p.lateMin} мин</span>` : ""}` : "") +
    `<br><span style="color:#666">📍 простой зачтётся в ${GEO_AT_PLACE_M} м</span>` +
    (p.prio ? "<br>⭐ приоритетный" : "") +
    (p.deadline ? `<br>⏰ до ${esc(p.deadline)}` : "") +
    (p.note ? `<br>${p.note}` : "");

  // ETA выданного заказа: по остатку его маршрута из позиции курьера
  // (примерно: расстояние по стопам / скорость × трафик + выдача до него)
  const outEtaMin = (o: Order, cour: Courier): number | null => {
    const stops = (cour.out_route?.stops || [])
      .filter(sp => sp.length > 2) as [number, number, string][];
    if (!cour.pos || !stops.length) return null;
    const idx = stops.findIndex(sp => sp[2] === o.id);
    if (idx < 0) return null;
    const pts: [number, number][] = [[cour.pos.lat, cour.pos.lng],
      ...stops.slice(0, idx + 1).map(sp => [sp[0], sp[1]] as [number, number])];
    let km = 0;
    for (let i = 0; i < pts.length - 1; i++) km += havKm(pts[i], pts[i + 1]);
    return km / ((state.settings.speed_kmh || 60) / 60) * (state.settings.traffic || 1.25)
      + (state.settings.handover_min ?? 5) * idx;
  };

  const planMap: Record<string, { color: string; label: string; popup: string }> = {};
  const plan = state.plan as Plan | null;
  if (plan) {
    const names = plan.routes.map(r => r.courier_name.toUpperCase());
    const tag = (name: string) => {
      for (let l = 1; l <= name.length; l++) {
        const t = name.slice(0, l);
        if (names.filter(n => n.slice(0, l) === t).length === 1) return t;
      }
      return name;
    };
    let k = 0;
    plan.routes.forEach(r => r.stops.forEach(s => {
      k += 1;
      planMap[s.order_id] = {
        color: r.color,
        label: `${tag(r.courier_name.toUpperCase())}${k}`,
        popup: popupHtml({ address: s.address, courier: r.courier_name,
                           eta: s.eta_clock, lateMin: s.late_min,
                           prio: s.prio, deadline: s.deadline }),
      };
    }));
  }

  const courierBalloon = (c: Courier) => {
    const r = routeOf(plan, c.id);
    const endClk = r && r.trips?.length ? r.trips[r.trips.length - 1].end_clock : undefined;
    const outN = c.out_route?.stops?.length || 0;
    return `<b>${esc(c.name)}</b><br>📍 ${fmtAge(c.pos!.ts)} назад${c.pos!.live ? " · live" : ""}` +
      (c.pos!.acc ? ` · ±${Math.round(c.pos!.acc)} м` : "") +
      (r ? `<br>Маршрут: ${r.count} зак. · ≈${Math.round(r.total_min)} мин · финиш ${endClk || "?"}` : "") +
      (!r && outN ? `<br>В развозке: ${outN} зак. (пунктир — выданные)` : "") +
      (!r && !outN && (c.out_route?.geom?.length || 0) > 1 ? "<br>↩ Возвращается на базу" : "");
  };

  // живые ссылки для обработчиков, привязанных к долгоживущим маркерам:
  // их замыкания не должны ссылаться на протухший план
  const planRef = useRef(plan); planRef.current = plan;
  const pickPtsRef = useRef(pickPts); pickPtsRef.current = pickPts;
  const couriersRef = useRef(state.couriers); couriersRef.current = state.couriers;

  /* маршрут выбранного курьера: точный из плана + пунктир выданных (#5) */
  const refreshSelRoute = () => {
    const ym = ymRef.current, map = mapRef.current;
    if (!ym || !map) return;
    if (L.current.routeLine) { map.geoObjects.remove(L.current.routeLine); L.current.routeLine = null; }
    const cid = selCid.current;
    if (!cid) return;
    const cur = couriersRef.current.find(c => c.id === cid);
    if (!cur) return;
    const r = routeOf(planRef.current, cid);
    const orr = cur.out_route;
    if (!r && !orr) return;
    const curPts = pickPtsRef.current;
    const color = cur.color || r?.color || DEFAULT_COURIER_COLOR;
    const lines: any[] = [];
    // пунктир: выданные заказы (или возврат на базу) — по дорожной геометрии,
    // если сохранили её на «Выдать», иначе по прямой через остановки
    if (orr && (orr.geom?.length || 0) > 1 || orr?.stops?.length) {
      const hp = orr.home || r?.home_point || curPts[0];
      if (hp) {
        const dashed: [number, number][] = orr.geom && orr.geom.length > 1
          ? orr.geom
          : [[hp.lat, hp.lng], ...(orr?.stops || []).map(sp => [sp[0], sp[1]] as [number, number]), [hp.lat, hp.lng]];
        lines.push(new ym.Polyline(dashed, {},
          { strokeColor: color, strokeWidth: 5, strokeOpacity: .85, strokeStyle: "1 3", zIndex: 30 }));
      }
    }
    // сплошная: актуальный маршрут из плана
    if (r && curPts.length) {
      const hp = r.home_point || curPts[0];
      const depot: [number, number] = [hp.lat, hp.lng];
      const pts: [number, number][] = [depot];
      (r.trips || []).forEach(tr => pts.push(...tripCoords(tr, depot)));
      lines.push(new ym.Polyline(pts, {},
        { strokeColor: r.color, strokeWidth: 6, strokeOpacity: 1, zIndex: 30 }));
    }
    if (!lines.length) return;
    if (lines.length > 1) {
      const col = new ym.Collection();
      lines.forEach(l => col.add(l));
      L.current.routeLine = col;
    } else {
      L.current.routeLine = lines[0];
    }
    map.geoObjects.add(L.current.routeLine);
  };
  // свежая версия refreshSelRoute для хендлеров карты, живущих с первой инициализации
  const refreshRef = useRef(refreshSelRoute); refreshRef.current = refreshSelRoute;

  useEffect(() => {
    const ym = ymRef.current, map = mapRef.current;
    if (!ready || !ym || !map) return;

    const pinLayout = (text: string, color: string) => {
      const cls = text.length > 2 ? " wide" : "";
      return ym.templateLayoutFactory.createClass(
        `<div class="pin"><span class="${cls.trim()}" style="background:${color}"><i>${text}</i></span></div>`);
    };
    const depotLayout = () => ym.templateLayoutFactory.createClass(
      `<div class="pin depot"><span><i>${WAREHOUSE_SVG}</i></span></div>`);

    /* атомарная замена маркера в слое: удалить старый, добавить новый,
       сохранить открытый балун (иначе плашка мигнёт при пересборке) */
    const swap = (store: Map<string, any>, key: string, create: () => any) => {
      const old = store.get(key);
      const wasOpen = !!old && old.balloon.isOpen();
      if (old) map.geoObjects.remove(old);
      const pm = create();
      map.geoObjects.add(pm);
      if (wasOpen) pm.balloon.open();
      store.set(key, pm);
      return pm;
    };

    /* 1) точки выдачи: склад, всегда поверх, крупнее (#9) */
    const seenP = new Set<string>();
    pickPts.forEach(p => {
      const key = p.id || p.name;
      seenP.add(key);
      const balloon = `<b>Место выдачи: ${esc(p.name)}</b><br>${esc(p.address)}`;
      let pm = L.current.depots.get(key);
      if (!pm) {
        pm = new ym.Placemark([p.lat, p.lng], { balloonContent: balloon },
          { iconLayout: depotLayout(), iconShape: { type: "Rectangle", coordinates: [[-21, -21], [21, 24]] },
            hideIconOnBalloonOpen: false, zIndex: 2000, cursor: "pointer" });
        const fx = () => { flyTo([p.lat, p.lng]); pm!.balloon.open(); };
        pm.events.add("click", fx);
        map.geoObjects.add(pm);
        L.current.depots.set(key, pm);
      } else {
        pm.geometry.setCoordinates([p.lat, p.lng]);
        pm.properties.set("balloonContent", balloon);
      }
    });
    for (const k of [...L.current.depots.keys()]) {
      if (!seenP.has(k)) { map.geoObjects.remove(L.current.depots.get(k)!); L.current.depots.delete(k); }
    }

    /* 1b) превью несохранённой точки: клик по карте в режиме пика */
    if (pickPreview) {
      seenP.add("__preview");
      let pv = L.current.depots.get("__preview");
      if (!pv) {
        pv = new ym.Placemark([pickPreview.lat, pickPreview.lng],
          { balloonContent: "<b>Новая точка (не сохранена)</b><br>Проверьте форму слева и нажмите «Сохранить»" },
          { preset: "islands#violetCircleDotIcon", zIndex: 1900, cursor: "help" });
        map.geoObjects.add(pv);
        L.current.depots.set("__preview", pv);
      } else {
        pv.geometry.setCoordinates([pickPreview.lat, pickPreview.lng]);
      }
    }

    /* 2) заказы: дубли — красные (#3), точки выбранного курьера — его цветом */
    const selCourier = selCid.current
      ? state.couriers.find(c => c.id === selCid.current) : null;
    const selOids = new Set<string>();
    if (selCourier) {
      (routeOf(plan, selCourier.id)?.stops || [])
        .forEach(sp => selOids.add(sp.order_id));
      (selCourier.out_route?.stops || [])
        .forEach(sp => { if (sp.length > 2) selOids.add(sp[2] as string); });
    }
    const selColor = selCourier?.color || DEFAULT_COURIER_COLOR;
    const seenO = new Set<string>();
    state.orders.forEach(o => {
      seenO.add(o.id);
      const p = planMap[o.id];
      // выданный заказ: его план-стоп уже удалён, но он в развозке у курьера
      const oCour = o.status === "out" && o.assigned
        ? state.couriers.find(c => c.id === o.assigned) : null;
      const mine = selOids.has(o.id);
      const color = mine ? selColor
        : oCour ? (oCour.color || ORDER_GRAY)
        : p ? p.color
        : (dupOids?.has(o.id) ? DUP_RED : ORDER_GRAY);
      const text = p ? p.label : "•";
      let content: string;
      if (p) {
        content = p.popup;
      } else if (oCour) {
        const etaMin = outEtaMin(o, oCour);
        content = popupHtml({ address: o.address, courier: oCour.name,
          eta: etaMin != null ? clockIn(etaMin) : undefined,
          lateMin: etaMin != null ? (lateByDeadline(o.deadline, etaMin) ?? undefined) : undefined,
          prio: o.prio, deadline: o.deadline });
      } else {
        content = popupHtml({ address: o.address,
          note: `(ещё не рассчитано)${dupOids?.has(o.id) ? ` · <span style="color:${DUP_RED}">дублирующийся адрес</span>` : ""}`,
          prio: o.prio, deadline: o.deadline });
      }
      const sig = `${text}|${color}`;
      let pm = L.current.orders.get(o.id);
      if (pm && L.current.orderSig.get(o.id) === sig) {
        pm.geometry.setCoordinates([o.lat, o.lng]);
        pm.properties.set("balloonContent", content);
        return;
      }
      const oid = o.id;
      pm = swap(L.current.orders, o.id, () => new ym.Placemark([o.lat, o.lng], { balloonContent: content },
        { iconLayout: pinLayout(text, color),
          iconShape: { type: "Rectangle", coordinates: [[-16, -16], [16, 20]] },
          hideIconOnBalloonOpen: false, zIndex: 500, cursor: "pointer" }));
      pm.events.add("click", () => { flyTo([o.lat, o.lng]); onMarkerClickRef.current(oid); });
      L.current.orderSig.set(oid, sig);
    });
    for (const k of [...L.current.orders.keys()]) {
      if (!seenO.has(k)) {
        map.geoObjects.remove(L.current.orders.get(k)!);
        L.current.orders.delete(k);
        L.current.orderSig.delete(k);
      }
    }

    /* 3) курьеры: меньше (#12), свои — ярко, чужие — тускло (#14), плавный ход (#11) */
    const seenC = new Set<string>();
    state.couriers.filter(c => c.pos).forEach(c => {
      seenC.add(c.id);
      const foreign = !!state.my_point && c.point_id !== state.my_point;
      const color = c.color || "#e8482b";
      const live = !!c.pos?.live;
      const sig = [c.name, color, live ? "L" : "", foreign ? "F" : "", selCid.current === c.id ? "S" : ""].join("|");
      const to: [number, number] = [c.pos!.lat, c.pos!.lng];
      let pm = L.current.couriers.get(c.id);
      if (!pm || L.current.courierSig.get(c.id) !== sig) {
        const cid = c.id;
        const html =
          `<div class="courier-marker${foreign ? " foreign" : ""}${selCid.current === c.id ? " sel" : ""}">` +
          `<div style="--c:${color}"><span class="cm-glow"></span>` +
          `${live && !foreign ? '<span class="cm-pulse"></span>' : ""}<span class="cm-body">${SCOOTER_SVG}</span>` +
          `<span class="cm-name">${esc(c.name)}</span></div></div>`;
        pm = swap(L.current.couriers, cid, () => new ym.Placemark(to, { balloonContent: courierBalloon(c) },
          { iconLayout: ym.templateLayoutFactory.createClass(html),
            iconShape: { type: "Rectangle", coordinates: [[-14, -14], [14, 20]] },
            hideIconOnBalloonOpen: false, zIndex: 1000, cursor: "pointer" }));
        pm.events.add("click", () => {
          const was = selCid.current;
          selCid.current = was === cid ? null : cid;
          refreshSelRoute();
          setSelTick(t => t + 1);
          const m = L.current.couriers.get(cid);
          const cur = m ? m.geometry.getCoordinates() : null;
          if (cur) flyTo([cur[0], cur[1]]);
          if (m && selCid.current && !m.balloon.isOpen()) m.balloon.open();
          // сигнатура с «S» изменилась — пересоберём маркер на следующем такте
          if (m) L.current.courierSig.set(cid, "");
        });
        // крестик балуна: закрыл — выбор снят, маршрут убран
        pm.balloon.events.add("userclose", () => {
          if (selCid.current === cid) {
            selCid.current = null;
            L.current.courierSig.set(cid, "");
            refreshRef.current();
            setSelTick(t => t + 1);
          }
        });
        L.current.courierSig.set(cid, sig);
        anims.current.delete(c.id);
        return;
      }
      pm.properties.set("balloonContent", courierBalloon(c));
      const cur = pm.geometry.getCoordinates() as [number, number];
      const jump = Math.abs(cur[0] - to[0]) > 0.02 || Math.abs(cur[1] - to[1]) > 0.02;
      if (jump) {
        pm.geometry.setCoordinates(to); // пропажа сигнала: без анимации
        anims.current.delete(c.id);
      } else if (cur[0] !== to[0] || cur[1] !== to[1]) {
        anims.current.set(c.id, { pm, from: [cur[0], cur[1]], to, start: performance.now() });
        ensureRaf();
      }
    });
    for (const k of [...L.current.couriers.keys()]) {
      if (!seenC.has(k)) {
        const pm = L.current.couriers.get(k)!;
        if (pm.balloon.isOpen()) pm.balloon.close();
        map.geoObjects.remove(pm);
        L.current.couriers.delete(k);
        L.current.courierSig.delete(k);
        anims.current.delete(k);
      }
    }

    /* 4) линии планов: пересобираем при смене состава ИЛИ пересчёте (solved_at),
       а для выбранного курьера — ещё и при изменении его выданной части */
    const selSig = `${selCid.current || "-"}:${selCourier?.out_route?.stops?.length || 0}:${selCourier?.out_route?.geom?.length || 0}`;
    const lineSig = selSig + "|" + (plan
      ? `${plan.solved_at}|` + plan.routes.map(r => [r.courier_id, r.color, (r.trips || [])
          .map(t => t.stops.map(s => s.order_id).join(",")).join(";")].join("|")).join("~")
      : "");
    if (lineSig !== L.current.routeSig) {
      L.current.routeSig = lineSig;
      L.current.lines.forEach(l => map.geoObjects.remove(l));
      L.current.lines = [];
      if (plan && pickPts.length) {
        plan.routes.forEach(r => (r.trips || []).forEach(tr => {
          const hp = r.home_point || pickPts[0];
          const depotPt: [number, number] = [hp.lat, hp.lng];
          const pts = tripCoords(tr, depotPt);
          L.current.lines.push(new ym.Polyline(pts, {},
            { strokeColor: r.color, strokeWidth: 4, strokeOpacity: 0.9, zIndex: 10 }));
        }));
        L.current.lines.forEach(l => map.geoObjects.add(l));
      }
      refreshSelRoute();
    }

    // первый автокадр; дальше позицию пользователя не трогаем
    if (!didInitialFit.current && (state.orders.length || pickPts.length)) {
      didInitialFit.current = true;
      fitAll(ym, map, state);
    }
  }, [ready, state, dupOids, selTick, pickPreview]);

  /* смена депо: сбрасываем выбор и подтягиваемся к новой точке (#1/#7) */
  useEffect(() => {
    if (!ready) return;
    if (state.my_point == null) return;
    if (lastMyPoint.current === state.my_point) return;
    const first = lastMyPoint.current === null;
    lastMyPoint.current = state.my_point;
    if (first) return; // первичная загрузка — кадр сделает автофит
    selCid.current = null;
    refreshSelRoute();
    setSelTick(t => t + 1);
    const map = mapRef.current;
    const pt = state.points?.find(p => p.id === state.my_point);
    if (map && pt) {
      map.panTo([pt.lat, pt.lng], { flying: true, delay: 0, duration: 500 });
      didInitialFit.current = false; // данные нового депо придут — пере-кадрируемся
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, state.my_point]);

  useEffect(() => {
    const ym = ymRef.current, map = mapRef.current;
    if (!ready || !ym || !map) return;
    if (fitSignal !== lastSignal.current) {
      lastSignal.current = fitSignal;
      fitAll(ym, map, state);
    }
  }, [fitSignal, ready, state]);

  /* клик по карточке в списке → летим к маркеру и открываем балун */
  useEffect(() => {
    if (!ready || !focus || !focus.n) return;
    const pm = focus.kind === "order"
      ? L.current.orders.get(focus.id)
      : focus.kind === "courier"
        ? L.current.couriers.get(focus.id)
        : L.current.depots.get(focus.id);
    if (!pm) return;
    const g = pm.geometry.getCoordinates() as [number, number];
    flyTo(g);
    pm.balloon.open();
    if (focus.kind === "courier") {
      selCid.current = focus.id;
      refreshSelRoute();
      setSelTick(t => t + 1);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, focus]);

  /* ---------- ховер карточки заказа → балун на карте (без пана) ---------- */
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    if (hoverRef.current && hoverOid !== hoverRef.current) {
      const old = L.current.orders.get(hoverRef.current);
      if (old && old.balloon.isOpen()) old.balloon.close();
      hoverRef.current = null;
    }
    if (hoverOid) {
      const pm = L.current.orders.get(hoverOid);
      if (pm) {
        hoverRef.current = hoverOid;
        if (!pm.balloon.isOpen()) pm.balloon.open();
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hoverOid, ready]);

  if (err) return <div className="map-err">{err}</div>;
  return <div ref={divRef} style={{ height: "100%", width: "100%" }} />;
}

function fitAll(ym: any, map: any, s: AppState) {
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
