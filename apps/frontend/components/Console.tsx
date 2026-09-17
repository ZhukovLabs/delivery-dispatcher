"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { type AppState, type Courier } from "@/lib/api";
import { addrKey } from "./console/format";
import { useDispatchState } from "./console/useDispatchState";
import { useUndo } from "./console/useUndo";
import { useToastAsk } from "./console/useToastAsk";
import { useConn } from "./console/useConn";
import { useWorkPoint } from "./console/useWorkPoint";
import { useMutate } from "./console/useMutate";
import { usePlanActions } from "./console/usePlanActions";
import { useOrderActions } from "./console/useOrderActions";
import { useMapInteractions } from "./console/useMapInteractions";
import { useDropOut } from "./console/useDropOut";
import { useUndoHotkey } from "./console/useUndoHotkey";
import TopBar from "./console/TopBar";
import LeftColumn from "./console/columns/LeftColumn";
import MapColumn from "./console/columns/MapColumn";
import PlanColumn from "./console/columns/PlanColumn";
import BindModal from "./console/BindModal";
import Sheet from "./console/Sheet";
import { AskDialog, HelpOverlay, PickBanner, SolveOverlay, Toast } from "./console/Overlays";

export default function Console() {
  /* оптимистичные патчи летящих мутаций: хук прогоняет через них каждый
     входящий WS-снимок, пока мутация в полёте — чужие события доходят,
     а снимки «до мутации» не откатывают локальный UI */
  const livePatches = useRef(new Map<string, (s: AppState) => AppState>());
  const { st, stLoading, stateErr, setSt, refresh, tick, dark, applyTheme } = useDispatchState(livePatches);

  const { toast, setToast, ask, setAsk, showToast, askConfirm } = useToastAsk();

  const { conn, prevConn, solveHide } = useConn();

  const { undoLen, lastLabel, doUndo, undoToast, pushUndo } = useUndo(refresh, showToast);

  useUndoHotkey(undoLen, doUndo);

  const {
    pickTarget, setPickTarget, fitSignal, bumpFit,
    hoverOid, setHoverOid, cardHl, setCardHl,
    focus, focusMap, pickPreview, registerPointPick, onEditChange, onMapPick, onMarkerClick,
  } = useMapInteractions({ setSt, showToast });

  const { workPoint, firstPid, wpSwitching, onWorkPoint } = useWorkPoint({
    st, refresh,
    clearHighlights: () => { setHoverOid(null); setCardHl(null); },
    refit: bumpFit,
  });

  const { mutate, syncN } = useMutate({ livePatches, st, setSt, showToast, refresh });

  const { solving, pinning, solve, pinOrder, unassignStop, courierToPlan, moveStop, resetSolving } =
    usePlanActions({ st, setSt, showToast, askConfirm, conn });

  useEffect(() => {
    if (prevConn.current === "offline" && conn === "online") {
      // состояние уже перезапрошено (invalidateQueries в useDispatchState),
      // юзеру остаётся короткое подтверждение; расчётный оверлей, который
      // «завис» из-за обрыва, снимается актуальным состоянием с сервера
      resetSolving();
      showToast("Связь восстановлена — данные синхронизированы");
    }
    prevConn.current = conn;
  }, [conn, showToast, resetSolving]);

  const { busyMode, assignOrderTo, giveRoute, applyAdvice } = useOrderActions({ st, setSt, mutate, showToast, undoToast });

  const { dropOut, dropOutOver, dropOutDrop, dropOutLeave } = useDropOut(unassignStop);

  const [dragOverCourier, setDragOverCourier] = useState<string | null>(null);
  const [dragOverRoute, setDragOverRoute] = useState<string | null>(null);
  const [bindFor, setBindFor] = useState<Courier | null>(null); // привязка Telegram
  const [sheetOpen, setSheetOpen] = useState(false);
  const [sheetTab, setSheetTab] = useState<"params" | "hist" | "prof" | "team">("prof");
  const [helpOpen, setHelpOpen] = useState(false);
  const [openAcc, setOpenAcc] = useState<"points" | "orders" | "couriers" | null>("orders");

  /* ---------- производные ---------- */
  // дубли адресов: два диспетчера могут добавить один адрес одновременно (#3)
  // (хук обязан стоять до раннего return при !st — Rules of Hooks)
  const dupOids = useMemo(() => {
    const n = new Map<string, number>();
    (st?.orders ?? []).forEach(o => { const k = addrKey(o.address); n.set(k, (n.get(k) || 0) + 1); });
    return new Set((st?.orders ?? []).filter(o => (n.get(addrKey(o.address)) || 0) > 1).map(o => o.id));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [st?.orders]);

  if (!st) {
    const err = stateErr as Error | null;
    return <div style={{ display: "flex", height: "100vh", alignItems: "center", justifyContent: "center", color: "#6d7688" }}>
      {err ? "Ошибка загрузки: " + err.message : stLoading ? "Загрузка…" : "Нет данных"}
    </div>;
  }

  const plan = st.plan;
  const active = st.couriers.filter(c => c.status !== "off").length;
  const miss = !(st.points || []).length ? "укажите место выдачи заказов"
    : !active ? "добавьте курьера («На базе» или «В пути»)"
    : !st.orders.length ? "добавьте готовые заказы" : "";

  return (
    <>
      <TopBar
        st={st} workPoint={workPoint} firstPid={firstPid} onWorkPoint={onWorkPoint}
        undoLen={undoLen} lastLabel={lastLabel} onUndo={() => void doUndo()}
        dark={dark} onTheme={applyTheme} syncing={syncN > 0}
        onHelp={() => setHelpOpen(true)}
        onProfile={() => { setSheetTab("prof"); setSheetOpen(true); }}
      />

      {pickTarget && <PickBanner kind={pickTarget} onCancel={() => setPickTarget(null)} />}

      <main className="console">
        <LeftColumn
          st={st} tick={tick} openAcc={openAcc} setOpenAcc={setOpenAcc}
          mutate={mutate} showToast={showToast} askConfirm={askConfirm}
          undoToast={undoToast} pushUndo={pushUndo} doUndo={doUndo}
          focusMap={focusMap} setHoverOid={setHoverOid} cardHl={cardHl} pinning={pinning}
          dupOids={dupOids} pickTarget={pickTarget} setPickTarget={setPickTarget}
          registerPointPick={registerPointPick} onEditChange={onEditChange}
          setBindFor={setBindFor} assignOrderTo={assignOrderTo}
          dragOverCourier={dragOverCourier} setDragOverCourier={setDragOverCourier}
          solving={solving} miss={miss}
          dropOut={dropOut} dropOutOver={dropOutOver} dropOutLeave={dropOutLeave} dropOutDrop={dropOutDrop}
          onSolveEnter={() => void solve()}
        />
        <MapColumn
          st={st} pickMode={!!pickTarget} onPick={ll => void onMapPick(ll)}
          fitSignal={fitSignal} hoverOid={hoverOid} onMarkerClick={onMarkerClick}
          dupOids={dupOids} focus={focus} pickPreview={pickPreview}
          wpSwitching={wpSwitching} refit={bumpFit}
          dropOut={dropOut} dropOutOver={dropOutOver} dropOutLeave={dropOutLeave} dropOutDrop={dropOutDrop}
        />
        <PlanColumn
          st={st} plan={plan} busyMode={busyMode}
          applyAdvice={applyAdvice} giveRoute={giveRoute}
          dragOverRoute={dragOverRoute} setDragOverRoute={setDragOverRoute}
          moveStop={moveStop} pinOrder={pinOrder} unassignStop={unassignStop}
          courierToPlan={courierToPlan} dropOutDrop={dropOutDrop}
        />
      </main>

      {sheetOpen && (
        <Sheet
          st={st} tab={sheetTab} setTab={setSheetTab} onClose={() => setSheetOpen(false)}
          setSt={setSt} showToast={showToast} askConfirm={askConfirm}
        />
      )}

      {((solving || !!st?.solving) && !solveHide) && <SolveOverlay offline={conn !== "online"} />}

      {helpOpen && <HelpOverlay onClose={() => setHelpOpen(false)} />}

      {bindFor && st && (
        <BindModal
          courier={bindFor}
          bot={st.tg?.bot || ""}
          seen={st.tg?.seen || []}
          onDone={s => { setSt(s); setBindFor(null); }}
          onClose={() => setBindFor(null)}
        />
      )}

      {ask && (
        <AskDialog ask={ask} onResolve={v => { setAsk(null); ask.resolve(v); }} />
      )}

      {toast && (
        <Toast toast={toast} onClose={() => setToast(null)} />
      )}
    </>
  );
}
