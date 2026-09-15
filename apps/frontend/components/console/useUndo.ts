"use client";

import { useCallback, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { UndoEntry } from "./format";

/* ---------- отмена действий (Ctrl+Z): стек последних операций ----------
   Каждый тип умеет «проиграть назад» своё действие через API. */

async function performUndo(u: UndoEntry): Promise<void> {
  if (u.type === "delOrder") await api("/api/orders", "POST", u.data);
  else if (u.type === "assign")
    for (const oid of u.data.order_ids as string[]) {
      try { await api(`/api/orders/${oid}/return`, "POST"); } catch { /* уже вернулся */ }
    }
  else if (u.type === "return") await api("/api/orders/assign", "POST", u.data);
  else if (u.type === "delCourier") await api("/api/couriers", "POST", u.data);
  else if (u.type === "cancel") await api("/api/orders", "POST", u.data);
}

export function useUndo(refresh: () => Promise<void>, showToast: (msg: string, err?: boolean, act?: { label: string; fn: () => void }) => void) {
  const undoStack = useRef<UndoEntry[]>([]);
  const [undoLen, setUndoLen] = useState(0);

  const doUndo = useCallback(async () => {
    const u = undoStack.current.pop();
    setUndoLen(undoStack.current.length);
    if (!u) return;
    try { await performUndo(u); await refresh(); showToast(`Отменено: ${u.label}`); }
    catch (e) { showToast((e as Error).message, true); }
  }, [refresh, showToast]);

  const undoToast = useCallback((msg: string, label: string, type: string, data: Record<string, unknown>) => {
    undoStack.current.push({ label, type, data });
    if (undoStack.current.length > 20) undoStack.current.shift();
    setUndoLen(undoStack.current.length);
    showToast(msg, false, { label: "Отменить", fn: () => void doUndo() });
  }, [doUndo, showToast]);

  const pushUndo = useCallback((label: string, type: string, data: Record<string, unknown>) => {
    undoStack.current.push({ label, type, data });
    if (undoStack.current.length > 20) undoStack.current.shift();
    setUndoLen(undoStack.current.length);
  }, []);

  return { undoLen, lastLabel: undoStack.current[undoStack.current.length - 1]?.label || "", doUndo, undoToast, pushUndo };
}
