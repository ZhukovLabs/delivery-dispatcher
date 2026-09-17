"use client";

import dynamic from "next/dynamic";
import { Loader2 } from "lucide-react";
import type { AppState } from "@/lib/api";
import ActivityFeed from "../ActivityFeed";
import type { MapViewProps } from "../../MapView";

const MapView = dynamic(() => import("../../MapView"), {
  ssr: false,
  loading: () => <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center", color: "#6d7688" }}>карта загружается…</div>,
});

export default function MapColumn({
  st, pickMode, onPick, fitSignal, hoverOid, onMarkerClick, dupOids, focus, pickPreview,
  wpSwitching, dropOut, dropOutOver, dropOutLeave, dropOutDrop, refit,
}: {
  st: AppState;
  pickMode: boolean;
  onPick: (ll: { lat: number; lng: number }) => void;
  fitSignal: number;
  hoverOid: string | null;
  onMarkerClick: (oid: string) => void;
  dupOids: MapViewProps["dupOids"];
  focus: MapViewProps["focus"];
  pickPreview: MapViewProps["pickPreview"];
  wpSwitching: boolean;
  dropOut: boolean;
  dropOutOver: (e: React.DragEvent) => void;
  dropOutLeave: (e: React.DragEvent) => void;
  dropOutDrop: (e: React.DragEvent) => void;
  refit: () => void;
}) {
  return (
    <section className={"col-map" + (dropOut ? " drop-out" : "")}
      onDragOver={dropOutOver}
      onDragLeave={dropOutLeave}
      onDrop={dropOutDrop}
    >
      <MapView
        state={st}
        pickMode={pickMode}
        onPick={onPick}
        fitSignal={fitSignal}
        hoverOid={hoverOid}
        onMarkerClick={onMarkerClick}
        dupOids={dupOids}
        focus={focus}
        pickPreview={pickPreview}
      />
      {wpSwitching && (
        <div className="map-loading" role="status" aria-live="polite">
          <Loader2 size={18} className="spin" /> Синхронизация места работы…
        </div>
      )}
      <button className="fit-btn" title="Показать все точки в кадре"
        style={{ position: "absolute", right: 12, bottom: 12, zIndex: 800 }}
        onClick={refit}>⤢</button>
      <ActivityFeed events={st?.events || []} />
      {dropOut && (
        <div className="drop-out-hint" role="status">Отпустите здесь — заказ вернётся в очередь готовых</div>
      )}
    </section>
  );
}
