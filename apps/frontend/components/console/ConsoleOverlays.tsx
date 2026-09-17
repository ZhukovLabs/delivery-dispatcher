"use client";

import { type AppState, type Courier } from "@/lib/api";
import { type WsConnState } from "@/lib/ws";
import type { AskState, ToastState } from "./format";
import BindModal from "./BindModal";
import Sheet from "./Sheet";
import { AskDialog, HelpOverlay, SolveOverlay, Toast } from "./Overlays";

export function ConsoleOverlays({ st, conn, solving, solveHide, helpOpen, setHelpOpen, bindFor, setBindFor, sheetOpen, setSheetOpen, sheetTab, setSheetTab, setSt, showToast, askConfirm, ask, setAsk, toast, setToast }: {
  st: AppState;
  conn: WsConnState;
  solving: boolean;
  solveHide: boolean;
  helpOpen: boolean;
  setHelpOpen: (v: boolean) => void;
  bindFor: Courier | null;
  setBindFor: (c: Courier | null) => void;
  sheetOpen: boolean;
  setSheetOpen: (v: boolean) => void;
  sheetTab: "params" | "hist" | "prof" | "team";
  setSheetTab: (v: "params" | "hist" | "prof" | "team") => void;
  setSt: (s: AppState) => void;
  showToast: (msg: string, err?: boolean, act?: { label: string; fn: () => void }) => void;
  askConfirm: (text: string, opts?: { ok?: string; danger?: boolean }) => Promise<boolean>;
  ask: AskState | null;
  setAsk: (v: AskState | null) => void;
  toast: ToastState | null;
  setToast: (v: ToastState | null) => void;
}) {
  return (<>
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
  </>);
}
