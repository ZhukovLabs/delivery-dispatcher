"use client";

import type { AppState, Courier } from "@/lib/api";
import PointsPanel from "../PointsPanel";
import OrdersPanel from "../OrdersPanel";
import CourierList from "../CourierList";

type Mutate = (method: string, path: string, body?: Record<string, unknown>) => Promise<void>;
type Confirm = (text: string, opts?: { ok?: string; danger?: boolean }) => Promise<boolean>;
type ShowToast = (msg: string, err?: boolean, act?: { label: string; fn: () => void }) => void;

export default function LeftColumn({
  st, tick, openAcc, setOpenAcc,
  mutate, showToast, askConfirm, undoToast, pushUndo, doUndo,
  focusMap, setHoverOid, cardHl, pinning, dupOids,
  pickTarget, setPickTarget, registerPointPick, onEditChange,
  setBindFor, assignOrderTo, dragOverCourier, setDragOverCourier,
  solving, miss, dropOut, dropOutOver, dropOutLeave, dropOutDrop, onSolveEnter,
}: {
  st: AppState;
  tick: number;
  openAcc: "points" | "orders" | "couriers" | null;
  setOpenAcc: React.Dispatch<React.SetStateAction<"points" | "orders" | "couriers" | null>>;
  mutate: Mutate;
  showToast: ShowToast;
  askConfirm: Confirm;
  undoToast: (msg: string, label: string, type: string, data: Record<string, unknown>) => void;
  pushUndo: (label: string, type: string, data: Record<string, unknown>) => void;
  doUndo: () => Promise<void>;
  focusMap: (kind: "order" | "courier" | "point", id: string) => void;
  setHoverOid: (oid: string | null) => void;
  cardHl: string | null;
  pinning: string | null;
  dupOids: Set<string>;
  pickTarget: "point" | "order" | null;
  setPickTarget: React.Dispatch<React.SetStateAction<"point" | "order" | null>>;
  registerPointPick: (cb: ((ll: { lat: number; lng: number }) => void) | null) => void;
  onEditChange: (pid: string | null) => void;
  setBindFor: (c: Courier | null) => void;
  assignOrderTo: (oid: string, cid: string) => Promise<void>;
  dragOverCourier: string | null;
  setDragOverCourier: (v: string | null) => void;
  solving: boolean;
  miss: string;
  dropOut: boolean;
  dropOutOver: (e: React.DragEvent) => void;
  dropOutLeave: (e: React.DragEvent) => void;
  dropOutDrop: (e: React.DragEvent) => void;
  onSolveEnter: () => void;
}) {
  return (
    <aside className={"col-left" + (dropOut ? " drop-out" : "")}
      onDragOver={dropOutOver}
      onDragLeave={dropOutLeave}
      onDrop={dropOutDrop}
    >
      <PointsPanel
        st={st} open={openAcc === "points"}
        onToggle={() => setOpenAcc(a => a === "points" ? null : "points")}
        mutate={mutate} showToast={showToast} askConfirm={askConfirm}
        focusMap={focusMap} pickTarget={pickTarget} setPickTarget={setPickTarget}
        registerPointPick={registerPointPick}
        onEditChange={onEditChange}
      />
      <div className="acc">
        <OrdersPanel
          st={st} tick={tick} open={openAcc === "orders"}
          onToggle={() => setOpenAcc(a => a === "orders" ? null : "orders")}
          mutate={mutate} showToast={showToast} undoToast={undoToast} pushUndo={pushUndo} doUndo={doUndo}
          focusMap={focusMap} setHoverOid={setHoverOid} cardHl={cardHl} pinning={pinning}
          dupOids={dupOids} pickTarget={pickTarget} setPickTarget={setPickTarget}
          onSolveEnter={onSolveEnter}
        />
        <CourierList
          st={st} open={openAcc === "couriers"}
          onToggle={() => setOpenAcc(a => a === "couriers" ? null : "couriers")}
          mutate={mutate} showToast={showToast} askConfirm={askConfirm} pushUndo={pushUndo}
          focusMap={focusMap} setBindFor={setBindFor} assignOrderTo={assignOrderTo}
          dragOverCourier={dragOverCourier} setDragOverCourier={setDragOverCourier}
        />
      </div>

      <div className="solvebox">
        <button className="solve" disabled={!!miss || solving || !!st?.solving} onClick={() => void onSolveEnter()}>
          {(solving || st?.solving) ? "⏳ Считаю…" : "⚡ Рассчитать развозку"}
        </button>
        <div className="solve-hint">{miss || ""}</div>
      </div>
    </aside>
  );
}
