"use client";

import { useEffect, useRef, useState } from "react";
import { fetchApi, type AppState, type Plan, type Courier, type Order } from "@/lib/api";
import { loadYmaps } from "./map/loadYmaps";
import {
  GEO_AT_PLACE_M, SCOOTER_SVG, WAREHOUSE_SVG, esc, ANIM_MS,
  DEFAULT_COURIER_COLOR, ORDER_GRAY, DUP_RED,
  havKm, clockIn, lateByDeadline, routeOf, nearestIdxFrom, trimAfter, fitAll,
} from "./map/mapUtils";
import { popupHtml, outEta, outRemain, buildPlanMap, courierBalloon } from "./map/mapCalc";
import { makeRefreshSelRoute, type RouteLayerState } from "./map/mapRoute";

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

export default function MapView({ state, pickMode, onPick, fitSignal, hoverOid, onMarkerClick, dupOids, focus, pickPreview }: MapViewProps) {
  const divRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<any>(null);
  const ymRef = useRef<any>(null);
  const [ready, setReady] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // зеркало выбора в стейт: клик по курьеру мгновенно перекрашивает его точки
  const [selTick, setSelTick] = useState(0);
  const [roadsTick, setRoadsTick] = useState(0);   // дороги плана доехали — перерисовать слой

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
    routeFetch: "",                    // последний запрос дорогой геометрии
    routeGeom: null as { key: string; coords: [number, number][] } | null,
    routeIdx: 0,                       // докуда подрезали хвост линии (монотонно)
    planGeom: new Map<string, [number, number][]>() as Map<string, [number, number][]>,
    orderCircle: null as any | null,   // радиус простоя у выбранного заказа
    orderCircleOid: null as string | null,
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
        // клик мимо маркеров — снять выбор курьера, убрать маршрут и радиус
        if (selCid.current) {
          selCid.current = null;
          refreshRef.current();
          setSelTick(t => t + 1);
        }
        if (L.current.orderCircle) {
          map.geoObjects.remove(L.current.orderCircle);
          L.current.orderCircle = null;
          L.current.orderCircleOid = null;
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
  // маркер едет к свежей геопозиции, и линия его маршрута подрезается тем же
  // кадром: хвост «пройденного» тает непрерывно, как в навигаторах
  const ensureRaf = () => {
    if (rafRef.current) return;
    const step = () => {
      const t0 = performance.now();
      let alive = false;
      anims.current.forEach((a, cid) => {
        const t = Math.min(1, (t0 - a.start) / ANIM_MS);
        const e = t < .5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
        const ip: [number, number] = [a.from[0] + (a.to[0] - a.from[0]) * e,
                                      a.from[1] + (a.to[1] - a.from[1]) * e];
        a.pm.geometry.setCoordinates(ip);
        if (cid === selCid.current) {
          const g = L.current.routeGeom as { key: string; coords: [number, number][] } | null;
          const line = L.current.routeLine as any;
          if (g && g.coords && g.coords.length > 1 && line && line.geometry
              && typeof line.geometry.setCoordinates === "function") {
            const idx = nearestIdxFrom(g.coords, ip, L.current.routeIdx || 0);
            if (idx > 0 && idx !== L.current.routeIdx) {
              L.current.routeIdx = idx;
              line.geometry.setCoordinates(g.coords.slice(idx));
            }
          }
        }
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

  const plan = state.plan as Plan | null;
  const planMap = buildPlanMap(plan);

  // живые ссылки для обработчиков, привязанных к долгоживущим маркерам:
  // их замыкания не должны ссылаться на протухший план
  const planRef = useRef(plan); planRef.current = plan;
  const pickPtsRef = useRef(pickPts); pickPtsRef.current = pickPts;
  const couriersRef = useRef(state.couriers); couriersRef.current = state.couriers;

  /* маршрут выбранного курьера: точный из плана + пунктир выданных (#5) */
  const refreshSelRoute = useRef(
    makeRefreshSelRoute({ ymRef, mapRef, L, selCid, couriersRef, planRef, pickPtsRef })
  ).current;
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

    // радиус простоя вокруг выбранного заказа (= TG_GEO_AT_PLACE бэкенда):
    // внутри круга 30-с простой курьера = вопрос «доставлен?»
    const showOrderRadius = (oid: string, co: [number, number]) => {
      if (L.current.orderCircle) map.geoObjects.remove(L.current.orderCircle);
      const c = new ym.Circle([co, GEO_AT_PLACE_M], {},
        { fillColor: "#3f7edf66", strokeColor: "#3f7edf", strokeWidth: 2,
          strokeOpacity: .9, fillOpacity: .22, clickable: false, zIndex: 40 });
      map.geoObjects.add(c);
      L.current.orderCircle = c;
      L.current.orderCircleOid = oid;
    };
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
    state.orders.forEach((o: Order) => {
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
        // курьер в 150 м от заказа — тот же радиус, что и зачёт простоя;
        // ближе — ETA не показываем, заказ фактически уже у адресата
        const onSite = !!(oCour.pos && o.lat != null && o.lng != null &&
          havKm([oCour.pos.lat, oCour.pos.lng], [o.lat, o.lng]) <= 0.15);
        const e = !onSite ? outEta(state, o, oCour) : null;
        content = popupHtml({ address: o.address, courier: oCour.name,
          eta: e ? clockIn(e.min) : undefined,
          inMin: e ? Math.round(e.min) : undefined,
          kmLeft: e ? e.km : undefined,
          lateMin: e ? (lateByDeadline(o.deadline, e.min) ?? undefined) : undefined,
          prio: o.prio, deadline: o.deadline, onSite });
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
      pm.events.add("click", () => {
        const co = pm.geometry.getCoordinates() as [number, number];
        showOrderRadius(oid, co);
        flyTo(co);
        onMarkerClickRef.current(oid);
      });
      L.current.orderSig.set(oid, sig);
    });
    for (const k of [...L.current.orders.keys()]) {
      if (!seenO.has(k)) {
        map.geoObjects.remove(L.current.orders.get(k)!);
        L.current.orders.delete(k);
        L.current.orderSig.delete(k);
        if (L.current.orderCircleOid === k && L.current.orderCircle) {
          map.geoObjects.remove(L.current.orderCircle);
          L.current.orderCircle = null;
          L.current.orderCircleOid = null;
        }
      }
    }

    /* 3) курьеры: меньше (#12), свои — ярко, чужие — тускло (#14), плавный ход (#11) */
    const seenC = new Set<string>();
    state.couriers.filter((c: Courier) => c.pos).forEach((c: Courier) => {
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
        pm = swap(L.current.couriers, cid, () => new ym.Placemark(to, { balloonContent: courierBalloon(state, plan, c) },
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
      pm.properties.set("balloonContent", courierBalloon(state, plan, c));
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
    const selSig = `${selCid.current || "-"}:${selCourier?.out_route?.stops?.length || 0}:${selCourier?.out_route?.geom?.length || 0}` +
      // позиция выбранного курьера в сигнатуре: гео-тик двигает его — линия
      // маршрута перерисовывается и обрезается по новой позиции (пройденное
      // исчезает); toFixed(4) ≈ 11 м — стоящего на месте не дёргает
      (selCourier?.pos ? `@${selCourier.pos.lat.toFixed(4)},${selCourier.pos.lng.toFixed(4)}` : "");
    const lineSig = roadsTick + "|" + selSig + "|" + (plan
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
          // дороги — норма: кэш по составу остановок; если геометрии в
          // плане нет (роутеры молчали при расчёте) — тянем /api/route
          // тем же каскадом и подменяем линию, прямыми — только резерв
          const oids = (tr.stops || []).map(s => s.order_id).join(",");
          const key = "plan|" + oids;
          const cached = (L.current.planGeom as Map<string, [number, number][]>).get(key);
          const base = cached || (tr.geometry && tr.geometry.length > 1 ? tr.geometry : null);
          const stops = (tr.stops || []).map(s => [s.lat, s.lng] as [number, number]);
          const pts = base ? trimAfter(base, stops[stops.length - 1] || depotPt)
            : [depotPt, ...stops];
          L.current.lines.push(new ym.Polyline(pts, {},
            { strokeColor: r.color, strokeWidth: 4, strokeOpacity: 0.9, zIndex: 10 }));
          if (!cached) {
            const qs = encodeURIComponent(
              [depotPt, ...stops].map(p => `${p[0].toFixed(6)},${p[1].toFixed(6)}`).join(";"));
            fetchApi(`/api/route?coords=${qs}`)
              .then(res => (res.ok ? res.json() : Promise.reject(new Error("route api"))))
              .then((j: { geometry?: [number, number][] }) => {
                if (!j.geometry || j.geometry.length < 2) return;
                (L.current.planGeom as Map<string, [number, number][]>).set(key, j.geometry);
                setRoadsTick(t => (t + 1) % 1e6);   // перерисовать слой с дорогами
              })
              .catch(() => { /* остались как есть — прямой резерв уже нарисован */ });
          }
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
  }, [ready, state, dupOids, selTick, roadsTick, pickPreview]);

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
