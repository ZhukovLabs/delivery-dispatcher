"use client";

import { useEffect, useRef, useState } from "react";
import { fmtAge, type AppState, type Plan, type Courier } from "@/lib/api";

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

export default function MapView({ state, pickMode, onPick, fitSignal, hoverOid, onMarkerClick, dupOids }: MapViewProps) {
  const divRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<any>(null);
  const ymRef = useRef<any>(null);
  const [ready, setReady] = useState(false);
  const [err, setErr] = useState<string | null>(null);

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
      }, { suppressMapOpenBlock: true });
      mapRef.current = map;
      // ховер-балуны не должны дёргать карту — подтягиваемся только по клику (#8)
      map.options.set("balloonAutoPan", false);
      map.events.add("click", (e: any) => {
        if (pickRef.current) {
          const c = e.get("coords") as [number, number];
          onPickRef.current({ lat: c[0], lng: c[1] });
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
    if (el) el.style.cursor = pickMode ? "crosshair" : "";
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
        popup: `<b>${esc(r.courier_name)}</b><br>${esc(s.address)}<br>≈${s.eta_clock || "?"}${s.late_min ? ` · <span style="color:#b3261e">опоздание ~${s.late_min} мин</span>` : ""}`,
      };
    }));
  }

  const courierBalloon = (c: Courier) => {
    const r = plan?.routes.find(x => x.courier_id === c.id);
    const endClk = r && r.trips?.length ? r.trips[r.trips.length - 1].end_clock : undefined;
    return `<b>${esc(c.name)}</b><br>📍 ${fmtAge(c.pos!.ts)} назад${c.pos!.live ? " · live" : ""}` +
      (c.pos!.acc ? ` · ±${Math.round(c.pos!.acc)} м` : "") +
      (r ? `<br>Маршрут: ${r.count} зак. · ≈${Math.round(r.total_min)} мин · финиш ${endClk || "?"}` : "");
  };

  /* маршрут выбранного курьера: жирная линия поверх остальных (#5) */
  const refreshSelRoute = () => {
    const ym = ymRef.current, map = mapRef.current;
    if (!ym || !map) return;
    if (L.current.routeLine) { map.geoObjects.remove(L.current.routeLine); L.current.routeLine = null; }
    const cid = selCid.current;
    if (!cid || !plan) return;
    const r = plan.routes.find(x => x.courier_id === cid);
    if (!r || !pickPts.length) return;
    const hp = r.home_point || pickPts[0];
    const depot: [number, number] = [hp.lat, hp.lng];
    const pts: [number, number][] = [depot];
    (r.trips || []).forEach(tr => {
      const seg: [number, number][] = tr.geometry && tr.geometry.length > 1
        ? tr.geometry : [depot, ...tr.stops.map(s => [s.lat, s.lng] as [number, number]), depot];
      pts.push(...seg);
    });
    L.current.routeLine = new ym.Polyline(pts, {},
      { strokeColor: r.color, strokeWidth: 6, strokeOpacity: 1, zIndex: 30 });
    map.geoObjects.add(L.current.routeLine);
  };

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
            zIndex: 2000, cursor: "pointer" });
        const fx = () => flyTo([p.lat, p.lng]);
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

    /* 2) заказы: дубли — красные (#3), метка маршрута или точка */
    const seenO = new Set<string>();
    state.orders.forEach(o => {
      seenO.add(o.id);
      const p = planMap[o.id];
      const color = p ? p.color : (dupOids?.has(o.id) ? "#d92d20" : "#8b95a8");
      const text = p ? p.label : "•";
      const content = p ? p.popup
        : `<b>${esc(o.address)}</b><br>(ещё не рассчитано)${dupOids?.has(o.id) ? "<br><span style=\"color:#d92d20\">дублирующийся адрес</span>" : ""}`;
      const sig = `${text}|${color}`;
      let pm = L.current.orders.get(o.id);
      if (pm && pm._sig === sig) {
        pm.geometry.setCoordinates([o.lat, o.lng]);
        pm.properties.set("balloonContent", content);
        return;
      }
      if (pm) map.geoObjects.remove(pm);
      pm = new ym.Placemark([o.lat, o.lng], { balloonContent: content },
        { iconLayout: pinLayout(text, color),
          iconShape: { type: "Rectangle", coordinates: [[-16, -16], [16, 20]] },
          zIndex: 500, cursor: "pointer" });
      pm._sig = sig;
      const oid = o.id;
      pm.events.add("click", () => { flyTo([o.lat, o.lng]); onMarkerClickRef.current(oid); });
      map.geoObjects.add(pm);
      L.current.orders.set(oid, pm);
    });
    for (const k of [...L.current.orders.keys()]) {
      if (!seenO.has(k)) { map.geoObjects.remove(L.current.orders.get(k)!); L.current.orders.delete(k); }
    }

    /* 3) курьеры: меньше (#12), свои — ярко, чужие — тускло (#14), плавный ход (#11) */
    const seenC = new Set<string>();
    state.couriers.filter(c => c.pos).forEach(c => {
      seenC.add(c.id);
      const foreign = !!state.my_point && (c.point_id || state.points?.[0]?.id) !== state.my_point;
      const color = c.color || "#e8482b";
      const live = !!c.pos?.live;
      const sig = [c.name, color, live ? "L" : "", foreign ? "F" : "", selCid.current === c.id ? "S" : ""].join("|");
      const to: [number, number] = [c.pos!.lat, c.pos!.lng];
      let pm = L.current.couriers.get(c.id);
      if (!pm || pm._sig !== sig) {
        if (pm) map.geoObjects.remove(pm);
        const html =
          `<div class="courier-marker${foreign ? " foreign" : ""}${selCid.current === c.id ? " sel" : ""}">` +
          `<div style="--c:${color}"><span class="cm-glow"></span>` +
          `${live && !foreign ? '<span class="cm-pulse"></span>' : ""}<span class="cm-body">${SCOOTER_SVG}</span>` +
          `<span class="cm-name">${esc(c.name)}</span></div></div>`;
        pm = new ym.Placemark(to, { balloonContent: courierBalloon(c) },
          { iconLayout: ym.templateLayoutFactory.createClass(html),
            iconShape: { type: "Rectangle", coordinates: [[-14, -14], [14, 20]] },
            zIndex: 1000, cursor: "pointer" });
        pm._sig = sig;
        const cid = c.id;
        pm.events.add("click", () => {
          const was = selCid.current;
          selCid.current = was === cid ? null : cid;
          refreshSelRoute();
          const m = L.current.couriers.get(cid);
          const cur = m ? m.geometry.getCoordinates() : null;
          if (cur) flyTo([cur[0], cur[1]]);
          if (m && selCid.current && !m.balloon.isOpen()) m.balloon.open();
          // сигнатура с «S» изменилась — пересоберём маркер на следующем такте
          m && (m._sig = "");
        });
        map.geoObjects.add(pm);
        L.current.couriers.set(cid, pm);
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
        anims.current.delete(k);
      }
    }

    /* 4) линии планов: пересобираем только при смене состава */
    const lineSig = plan
      ? plan.routes.map(r => [r.courier_id, r.color, (r.trips || [])
          .map(t => t.stops.map(s => s.order_id).join(",")).join(";")].join("|")).join("~")
      : "";
    if (lineSig !== L.current.routeSig) {
      L.current.routeSig = lineSig;
      L.current.lines.forEach(l => map.geoObjects.remove(l));
      L.current.lines = [];
      if (plan && pickPts.length) {
        plan.routes.forEach(r => (r.trips || []).forEach(tr => {
          const hp = r.home_point || pickPts[0];
          const depotPt: [number, number] = [hp.lat, hp.lng];
          const straight: [number, number][] = [depotPt, ...tr.stops.map(s => [s.lat, s.lng] as [number, number]), depotPt];
          const pts = tr.geometry && tr.geometry.length > 1 ? tr.geometry : straight;
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
  }, [ready, state, dupOids]);

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
