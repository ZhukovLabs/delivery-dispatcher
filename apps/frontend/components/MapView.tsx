"use client";

import { useEffect, useRef, useState } from "react";
import type { AppState, Plan } from "@/lib/api";

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

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const posAge = (ts: number) => {
  const m = Math.max(0, Math.round((Date.now() - ts * 1000) / 60000));
  return m === 0 ? "только что" : `${m} мин назад`;
};

export default function MapView({ state, pickMode, onPick, fitSignal, hoverOid, onMarkerClick }: MapViewProps) {
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

  const didInitialFit = useRef(false);
  const lastSignal = useRef(fitSignal);
  const hoverRef = useRef<string | null>(null);

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
      map.events.add("click", (e: any) => {
        if (pickRef.current) {
          const c = e.get("coords") as [number, number];
          onPickRef.current({ lat: c[0], lng: c[1] });
        }
      });
      map.container.fitToViewport();
      setReady(true);
    }).catch(() => !dead && setErr("Карта не загрузилась — проверьте интернет"));
    return () => { dead = true; if (mapRef.current) { mapRef.current.destroy(); mapRef.current = null; } };
  }, []);

  useEffect(() => {
    const el = divRef.current;
    if (el) el.style.cursor = pickMode ? "crosshair" : "";
    const t = setTimeout(() => mapRef.current?.container.fitToViewport(), 120);
    return () => clearTimeout(t);
  }, [pickMode, ready]);

  /* ---------- содержимое: маркеры и линии ---------- */
  const d = state.depot;
  const pickPts: { name: string; address: string; lat: number; lng: number }[] =
    state.points?.length ? state.points
      : d ? [{ name: "Основная", address: d.address, lat: d.lat, lng: d.lng }] : [];
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

  useEffect(() => {
    const ym = ymRef.current, map = mapRef.current;
    if (!ready || !ym || !map) return;
    map.geoObjects.removeAll();

    const pinLayout = (text: string, color: string) => {
      const cls = text.length > 2 ? " wide" : "";
      return ym.templateLayoutFactory.createClass(
        `<div class="pin"><span class="${cls.trim()}" style="background:${color}"><i>${text}</i></span></div>`);
    };

    pickPts.forEach(p => {
      const pm = new ym.Placemark([p.lat, p.lng],
        { balloonContent: `<b>Место выдачи: ${esc(p.name)}</b><br>${esc(p.address)}` },
        { iconLayout: pinLayout("🏠", "#141c2b"), iconShape: { type: "Rectangle", coordinates: [[-13, -30], [13, 0]] } });
      map.geoObjects.add(pm);
    });

    state.orders.forEach((o, i) => {
      const p = planMap[o.id];
      const pm = new ym.Placemark([o.lat, o.lng],
        { balloonContent: p ? p.popup : `<b>${esc(o.address)}</b><br>(ещё не рассчитано)` },
        {
          iconLayout: pinLayout(p ? p.label : String(i + 1), p ? p.color : "#8b95a8"),
          iconShape: { type: "Rectangle", coordinates: [[-16, -30], [16, 0]] },
          cursor: "pointer",
        });
      pm.events.add("click", () => onMarkerClickRef.current(o.id));
      map.geoObjects.add(pm);
    });

    state.couriers.filter(c => c.pos).forEach(c => {
      const color = c.color || "#e8482b";
      const live = !!c.pos?.live;
      const html =
        `<div class="courier-marker"><div style="--c:${color}"><span class="cm-glow"></span>` +
        `${live ? '<span class="cm-pulse"></span>' : ""}<span class="cm-body">${SCOOTER_SVG}</span>` +
        `<span class="cm-name">${esc(c.name)}</span></div></div>`;
      const pm = new ym.Placemark([c.pos!.lat, c.pos!.lng],
        { balloonContent: `<b>${esc(c.name)}</b><br>📍 ${posAge(c.pos!.ts)}${live ? " · live" : ""}${c.pos!.acc ? ` · ±${Math.round(c.pos!.acc)} м` : ""}` },
        {
          iconLayout: ym.templateLayoutFactory.createClass(html),
          iconShape: { type: "Rectangle", coordinates: [[-17, -17], [17, 17]] },
          zIndex: 1000, cursor: "pointer",
        });
      map.geoObjects.add(pm);
    });

    if (plan && pickPts.length) {
      plan.routes.forEach(r => (r.trips || []).forEach(tr => {
        const hp = r.home_point || pickPts[0];
        const depotPt: [number, number] = [hp.lat, hp.lng];
        const straight: [number, number][] = [depotPt, ...tr.stops.map(s => [s.lat, s.lng] as [number, number]), depotPt];
        const pts = tr.geometry && tr.geometry.length > 1 ? tr.geometry : straight;
        map.geoObjects.add(new ym.Polyline(pts, {}, { strokeColor: r.color, strokeWidth: 4, strokeOpacity: 0.9 }));
      }));
    }

    // первый автокадр; дальше позицию пользователя не трогаем
    if (!didInitialFit.current && (state.orders.length || pickPts.length)) {
      didInitialFit.current = true;
      fitAll(ym, map, state);
    }
  }, [ready, state]);

  useEffect(() => {
    const ym = ymRef.current, map = mapRef.current;
    if (!ready || !ym || !map) return;
    if (fitSignal !== lastSignal.current) {
      lastSignal.current = fitSignal;
      fitAll(ym, map, state);
    }
  }, [fitSignal, ready, state]);

  /* ---------- ховер карточки заказа → балун на карте ---------- */
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    if (hoverRef.current && hoverRef.current !== hoverOid) {
      hoverRef.current = null;
      if (map.balloon.isOpen()) map.balloon.close();
    }
    if (hoverOid) {
      const o = state.orders.find(x => x.id === hoverOid);
      const p = planMap[hoverOid];
      if (o) {
        hoverRef.current = hoverOid;
        map.balloon.open([o.lat, o.lng],
          p ? p.popup : `<b>${esc(o.address)}</b><br>(ещё не рассчитано)`);
      }
    }
  }, [hoverOid, ready, state]);

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
