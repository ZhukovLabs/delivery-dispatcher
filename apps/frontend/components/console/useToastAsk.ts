"use client";

import { useCallback, useRef, useState } from "react";
import type { AskState, ToastState } from "./format";

export function useToastAsk() {
  const [toast, setToast] = useState<ToastState | null>(null);
  const toastT = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [ask, setAsk] = useState<AskState | null>(null);

  const showToast = useCallback((msg: string, err = false, act?: ToastState["act"]) => {
    setToast({ msg, err, act });
    if (toastT.current) clearTimeout(toastT.current);
    toastT.current = setTimeout(() => setToast(null), act ? 6000 : 3400);
  }, []);

  const askConfirm = useCallback((text: string, opts: { ok?: string; danger?: boolean } = {}) =>
    new Promise<boolean>(resolve => {
      setAsk({ text, ok: opts.ok || "Да", danger: !!opts.danger, resolve });
    }), []);

  return { toast, setToast, ask, setAsk, showToast, askConfirm };
}
