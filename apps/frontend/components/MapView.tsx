"use client";

import { useEffect, useRef } from "react";
import L from "leaflet";
import { MapContainer, Marker, Polyline, Popup, TileLayer, useMap, useMapEvents } from "react-leaflet";
import type { AppState, Plan } from "@/lib/api";

export interface MapViewProps {
  state: AppState;
  pickMode: boolean;
  onPick: (ll: { lat: number; lng: number }) => void;
  fitSignal: number;
  hoverOid: string | null;
  onMarkerClick: (oid: string) => void;
}

const pin = (text: string, color: string) =>
  L.divIcon({
    className: "pin",
    iconSize: [26, 26],
    iconAnchor: [13, 26],
    html: `<span class="${text.length > 2 ? "wide" : ""}" style="background:${color}"><i>${text}</i></span>`,
  });

const courierPin = (color: string) =>
  L.divIcon({
    className: "pin courier-pin",
    iconSize: [26, 26],
    iconAnchor: [13, 26],
    html: `<span style="background:${color};border:2px solid #fff;box-shadow:0 1px 6px rgba(0,0,0,.45)"><i>🛵</i></span>`,
  });

const posAge = (ts: number) => {
  const m = Math.max(0, Math.round((Date.now() - ts * 1000) / 60000));
  return m === 0 ? "только что" : `${m} мин назад`;
};

function fitNow(map: L.Map, s: AppState) {
  const d = s.depot;
  if (!s.orders.length && !d) return;
  const bounds = L.latLngBounds(s.orders.map(o => [o.lat, o.lng] as [number, number]));
  if (d) bounds.extend([d.lat, d.lng] as [number, number]);
  map.fitBounds(bounds.pad(0.18), { maxZoom: 16 });
}

function Controller({ state, pickMode, onPick, fitSignal }: Required<Pick<MapViewProps, "state" | "pickMode" | "onPick" | "fitSignal">>) {
  const map = useMap();
  const didInitial = useRef(false);
  const lastSignal = useRef(fitSignal);

  // первый автокадр, дальше позицию пользователя не трогаем
  useEffect(() => {
    const total = state.orders.length + (state.depot ? 1 : 0);
    if (total && !didInitial.current) {
      didInitial.current = true;
      fitNow(map, state);
    }
  }, [state.orders.length, state.depot, map, state]);

  useEffect(() => {
    if (fitSignal !== lastSignal.current) {
      lastSignal.current = fitSignal;
      fitNow(map, state);
    }
  }, [fitSignal, map, state]);

  useMapEvents({
    click(e) {
      if (pickMode) onPick(e.latlng);
    },
  });
  useEffect(() => {
    const el = map.getContainer();
    el.style.cursor = pickMode ? "crosshair" : "";
    const t = setTimeout(() => map.invalidateSize(), 120);
    return () => clearTimeout(t);
  }, [pickMode, map]);
  return null;
}

function HoverPopup({ hoverOid, markers }: { hoverOid: string | null; markers: Map<string, L.Marker> }) {
  const prev = useRef<string | null>(null);
  useEffect(() => {
    if (prev.current && prev.current !== hoverOid) {
      const m = markers.get(prev.current);
      if (m) { m.setZIndexOffset(0); m.getElement()?.querySelector(".pin")?.classList.remove("hl"); m.closePopup(); }
    }
    if (hoverOid) {
      const m = markers.get(hoverOid);
      if (m) { m.setZIndexOffset(1000); m.getElement()?.querySelector(".pin")?.classList.add("hl"); m.openPopup(); }
    }
    prev.current = hoverOid;
  }, [hoverOid, markers]);
  return null;
}

export default function MapView({ state, pickMode, onPick, fitSignal, hoverOid, onMarkerClick }: MapViewProps) {
  const markers = useRef(new Map<string, L.Marker>()).current;
  const d = state.depot;

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
        popup: `<b>${r.courier_name}</b><br>${s.address}<br>≈${s.eta_clock || "?"}${s.late_min ? ` · <span style="color:#b3261e">опоздание ~${s.late_min} мин</span>` : ""}`,
      };
    }));
  }

  return (
    <MapContainer
      center={[52.4345, 31.0137]}
      zoom={13}
      doubleClickZoom={false}
      style={{ height: "100%", background: "#dfe6ee" }}
    >
      <TileLayer
        url="https://mt{s}.google.com/vt/lyrs=m&hl=ru&x={x}&y={y}&z={z}"
        subdomains={["0", "1", "2", "3"]}
        maxZoom={20}
        attribution="© Google"
      />
      <Controller state={state} pickMode={pickMode} onPick={onPick} fitSignal={fitSignal} />

      {d && (
        <Marker position={[d.lat, d.lng]} icon={pin("🏠", "#141c2b")}>
          <Popup autoPan={false}><b>Депо</b><br />{d.address}</Popup>
        </Marker>
      )}
      {state.orders.map((o, i) => {
        const p = planMap[o.id];
        return (
          <Marker
            key={o.id}
            position={[o.lat, o.lng]}
            icon={pin(p ? p.label : String(i + 1), p ? p.color : "#8b95a8")}
            ref={m => { if (m) markers.set(o.id, m); else markers.delete(o.id); }}
            eventHandlers={{ click: () => onMarkerClick(o.id) }}
          >
            <Popup autoPan={false}>
              {p
                ? <span dangerouslySetInnerHTML={{ __html: p.popup }} />
                : <><b>{o.address}</b><br />(ещё не рассчитано)</>}
            </Popup>
          </Marker>
        );
      })}
      {state.couriers.filter(c => c.pos).map(c => (
        <Marker
          key={`cr-${c.id}`}
          position={[c.pos!.lat, c.pos!.lng]}
          icon={courierPin(c.color || "#e8482b")}
          zIndexOffset={900}
        >
          <Popup autoPan={false}>
            <b>{c.name}</b><br />
            📍 {posAge(c.pos!.ts)}{c.pos!.live ? " · live" : ""}
            {c.pos!.acc ? ` · ±${Math.round(c.pos!.acc)} м` : ""}
          </Popup>
        </Marker>
      ))}
      {plan && d && plan.routes.flatMap((r, ri) =>
        (r.trips || []).map((tr, ti) => {
          const depotPt: [number, number] = [d.lat, d.lng];
          const straight: [number, number][] = [depotPt, ...tr.stops.map(s => [s.lat, s.lng] as [number, number]), depotPt];
          const pts = tr.geometry && tr.geometry.length > 1 ? tr.geometry : straight;
          return <Polyline key={`${ri}-${ti}`} positions={pts} pathOptions={{ color: r.color, weight: 4, opacity: 0.9 }} />;
        })
      )}
      <HoverPopup hoverOid={hoverOid} markers={markers} />
    </MapContainer>
  );
}
