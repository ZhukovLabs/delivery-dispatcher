"use client";

import type { Order } from "@/lib/api";
import type { MapCtx } from "./mapLayers";
import { makePinLayout, swap } from "./mapLayers";
import {
  GEO_AT_PLACE_M, DEFAULT_COURIER_COLOR, ORDER_GRAY, DUP_RED,
  havKm, clockIn, lateByDeadline, routeOf,
} from "./mapUtils";
import { popupHtml, outEta } from "./mapCalc";

/* 2) заказы: дубли — красные (#3), точки выбранного курьера — его цветом */
export function syncOrders(ctx: MapCtx) {
  const { ym, map, state, plan, planMap, dupOids, L, selCid, selCourier, flyTo, onMarkerClickRef } = ctx;
  const pinLayout = makePinLayout(ym);

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
    pm = swap(map, L.current.orders, o.id, () => new ym.Placemark([o.lat, o.lng], { balloonContent: content },
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
}
