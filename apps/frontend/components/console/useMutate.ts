"use client";

import { useRef, useState } from "react";
import { api, type AppState } from "@/lib/api";
import { optimisticFor } from "./optimistic";

export function useMutate(deps: {
  livePatches: { current: Map<string, (s: AppState) => AppState> };
  st: AppState | null;
  setSt: (s: AppState) => void;
  showToast: (msg: string, err?: boolean) => void;
  refresh: () => Promise<void>;
}) {
  const { livePatches, st, setSt, showToast, refresh } = deps;
  /* ---------- мутации с оптимистичным патчем ---------- */
  // даблклик не должен слать вторую мутацию того же действия: пока запрос
  // в полёте, повтор по тому же method+path+body игнорируем. Тело — часть
  // ключа: выдача второму курьеру — другое действие, её нельзя глотать
  const inflight = useRef(new Set<string>());
  const [syncN, setSyncN] = useState(0); // летящие мутации — индикатор в шапке
  const mutate = async (method: string, path: string, body?: Record<string, unknown>) => {
    const key = method + " " + path + (body === undefined ? "" : " " + JSON.stringify(body));
    if (inflight.current.has(key)) return;
    inflight.current.add(key);
    setSyncN(n => n + 1);
    const opt = st ? optimisticFor(method, path, body as Record<string, any> | undefined) : undefined;
    if (opt) livePatches.current.set(key, opt);
    try {
      if (opt && st) setSt(opt(st)); // мгновенный отклик; rev остаётся серверным
      setSt(await api<AppState>(path, method, body));
    }
    catch (e) { showToast((e as Error).message, true); if (opt) void refresh(); }
    finally { livePatches.current.delete(key); inflight.current.delete(key); setSyncN(n => n - 1); }
  };
  return { mutate, syncN };
}
