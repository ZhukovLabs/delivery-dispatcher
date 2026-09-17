"use client";

import { useEffect } from "react";

export function useUndoHotkey(undoLen: number, doUndo: () => Promise<void>) {
  /* Ctrl+Z — отмена последнего действия */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "z") return;
      const t = (document.activeElement || {}).tagName || "";
      if (/INPUT|TEXTAREA|SELECT/.test(t)) return;
      if (!undoLen) return;
      e.preventDefault();
      void doUndo();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [undoLen, doUndo]);
}
