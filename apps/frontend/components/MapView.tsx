"use client";

import { useEffect, useRef, useState } from "react";
import { type AppState, type Plan } from "@/lib/api";
import { buildPlanMap } from "./map/mapCalc";
import { makeRefreshSelRoute } from "./map/mapRoute";
import { fitAll } from "./map/mapUtils";
import type { AnimMap, LayerState, MapCtx } from "./map/mapLayers";
import { makeEnsureRaf } from "./map/mapAnim";
import { useInitMap } from "./map/mapInit";
import { usePickCross, useDepotChange, useFitOnSignal, useFocusMarker, useHoverBalloon } from "./map/mapEffects";
import { syncDepots } from "./map/mapDepots";
import { syncOrders } from "./map/mapOrders";
import { syncCouriers } from "./map/mapCouriers";
import { syncPlanLines } from "./map/mapPlanLines";

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
  const bumpSel = () => setSelTick(t => t + 1);
  const bumpRoads = () => setRoadsTick(t => (t + 1) % 1e6);

  // свежие колбэки для обработчиков карты без пересоздания карты
  const pickRef = useRef(pickMode);
  const onPickRef = useRef(onPick);
  const onMarkerClickRef = useRef(onMarkerClick);
  pickRef.current = pickMode;
  onPickRef.current = onPick;
  onMarkerClickRef.current = onMarkerClick;

  // инкрементальные слои: маркеры не пересоздаются на каждом обновлении
  const L = useRef<LayerState>({
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
  const anims = useRef<AnimMap>(new Map());
  const rafRef = useRef(0);
  const selCid = useRef<string | null>(null); // выбранный курьер: подсветка его маршрута
  const didInitialFit = useRef(false);
  const lastSignal = useRef(fitSignal);
  const hoverRef = useRef<string | null>(null);
  const lastMyPoint = useRef<string | null>(null);

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

  useInitMap({ divRef, mapRef, ymRef, pickRef, onPickRef, selCid, refreshRef, L, rafRef, setReady, setErr, bumpSel });

  usePickCross({ divRef, mapRef, pickMode, ready });

  const ensureRaf = makeEnsureRaf({ anims, L, selCid, rafRef });

  const flyTo = (coords: [number, number]) => {
    const map = mapRef.current;
    if (!map) return;
    if (map.getZoom() < 15) map.setZoom(15, { smooth: true, duration: 200 });
    map.panTo(coords, { flying: true, delay: 0, duration: 420 });
  };

  useEffect(() => {
    const ym = ymRef.current, map = mapRef.current;
    if (!ready || !ym || !map) return;
    const selCourier = selCid.current
      ? state.couriers.find(c => c.id === selCid.current) ?? null : null;
    const ctx: MapCtx = {
      ym, map, state, plan, planMap, pickPts, dupOids, pickPreview,
      selCid, selCourier, L, anims, roadsTick, bumpRoads, bumpSel,
      flyTo, refreshSelRoute, refreshRef, onMarkerClickRef, ensureRaf,
    };
    syncDepots(ctx);
    syncOrders(ctx);
    syncCouriers(ctx);
    syncPlanLines(ctx);

    // первый автокадр; дальше позицию пользователя не трогаем
    if (!didInitialFit.current && (state.orders.length || pickPts.length)) {
      didInitialFit.current = true;
      fitAll(ym, map, state);
    }
  }, [ready, state, dupOids, selTick, roadsTick, pickPreview]);

  useDepotChange({ ready, state, mapRef, lastMyPoint, selCid, refreshSelRoute, bumpSel, didInitialFit });

  useFitOnSignal({ ymRef, mapRef, ready, fitSignal, lastSignal, state });

  useFocusMarker({ ready, focus, L, flyTo, selCid, refreshSelRoute, bumpSel });

  useHoverBalloon({ mapRef, ready, hoverOid, hoverRef, L });

  if (err) return <div className="map-err">{err}</div>;
  return <div ref={divRef} style={{ height: "100%", width: "100%" }} />;
}
