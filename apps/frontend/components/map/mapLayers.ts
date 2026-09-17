"use client";

import type { MutableRefObject } from "react";
import type { AppState, Plan, Courier, PickPoint } from "@/lib/api";
import type { PlanPin } from "./mapCalc";
import type { RouteLayerState } from "./mapRoute";
import { WAREHOUSE_SVG } from "./mapUtils";

export type AnimEntry = { pm: any; from: [number, number]; to: [number, number]; start: number };
export type AnimMap = Map<string, AnimEntry>;

export interface LayerState extends RouteLayerState {
  depots: Map<string, any>;
  orders: Map<string, any>;
  orderSig: Map<string, string>;
  courierSig: Map<string, string>;
  lines: any[];
  planGeom: Map<string, [number, number][]>;
  orderCircle: any | null;
  orderCircleOid: string | null;
}

export interface MapCtx {
  ym: any;
  map: any;
  state: AppState;
  plan: Plan | null;
  planMap: Record<string, PlanPin>;
  pickPts: PickPoint[];
  dupOids?: Set<string>;
  pickPreview?: { lat: number; lng: number } | null;
  selCid: MutableRefObject<string | null>;
  selCourier: Courier | null;
  L: MutableRefObject<LayerState>;
  anims: MutableRefObject<AnimMap>;
  roadsTick: number;
  bumpRoads: () => void;
  bumpSel: () => void;
  flyTo: (coords: [number, number]) => void;
  refreshSelRoute: () => void;
  refreshRef: MutableRefObject<() => void>;
  onMarkerClickRef: MutableRefObject<(oid: string) => void>;
  ensureRaf: () => void;
}

export const makePinLayout = (ym: any) => (text: string, color: string) => {
  const cls = text.length > 2 ? " wide" : "";
  return ym.templateLayoutFactory.createClass(
    `<div class="pin"><span class="${cls.trim()}" style="background:${color}"><i>${text}</i></span></div>`);
};

export const makeDepotLayout = (ym: any) => () => ym.templateLayoutFactory.createClass(
  `<div class="pin depot"><span><i>${WAREHOUSE_SVG}</i></span></div>`);

/* атомарная замена маркера в слое: удалить старый, добавить новый,
   сохранить открытый балун (иначе плашка мигнёт при пересборке) */
export const swap = (map: any, store: Map<string, any>, key: string, create: () => any) => {
  const old = store.get(key);
  const wasOpen = !!old && old.balloon.isOpen();
  if (old) map.geoObjects.remove(old);
  const pm = create();
  map.geoObjects.add(pm);
  if (wasOpen) pm.balloon.open();
  store.set(key, pm);
  return pm;
};
