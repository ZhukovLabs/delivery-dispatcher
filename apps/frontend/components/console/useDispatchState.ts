"use client";

import { useCallback, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, fetchApi, type AppState } from "@/lib/api";
import { ensureWsToken, getSocket } from "@/lib/ws";

/* ---------- данные консоли: кэш + живые обновления + тикер возраста + тема ----------
   План считается ТОЛЬКО по кнопке «Рассчитать». Живые обновления (несколько
   админов, геолокации курьеров) — socket.io: сервер при любом изменении
   состояния пушит "state" с payload'ом депо подписчика (см. lib/ws.ts). */

export function useDispatchState() {
  const qc = useQueryClient();
  const { data: stData, error: stateErr, isPending: stLoading } = useQuery({
    queryKey: ["state"],
    queryFn: () => api<AppState>("/api/state"),
    staleTime: 10000,
    retry: 1,
    refetchOnWindowFocus: "always",
  });

  // живые обновления: сервер пушит payload целиком — кладём его в кэш напрямую
  useEffect(() => {
    let off: (() => void) | null = null;
    let cancelled = false;
    void (async () => {
      if (!(await ensureWsToken()) || cancelled) return;
      const s = getSocket();
      const onState = (d: AppState) => { qc.setQueryData(["state"], d); };
      const onConnect = () => { void qc.invalidateQueries({ queryKey: ["state"] }); }; // подтянуть пропущенное за простой
      s.on("state", onState);
      s.on("connect", onConnect);
      if (!s.connected) s.connect();
      off = () => { s.off("state", onState); s.off("connect", onConnect); };
    })();
    return () => { cancelled = true; off?.(); };
  }, [qc]);

  const st = stData ?? null;
  const setSt = useCallback((s: AppState) => { qc.setQueryData(["state"], s); }, [qc]);
  const refresh = useCallback(async () => { await qc.invalidateQueries({ queryKey: ["state"] }); }, [qc]);

  const [tick, setTick] = useState(0); // ежеминутное обновление возраста/бейджей
  const [dark, setDark] = useState(false);

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
    const iv = setInterval(() => { if (!document.hidden) setTick(t => t + 1); }, 60000);
    /* free-тариф облака засыпает через 15 мин тишины: пока вкладка открыта — лёгкий пинг, чтобы не ждать холодный старт */
    const hb = /^(localhost|127\.)/.test(location.hostname)
      ? null
      : setInterval(() => { if (!document.hidden) void fetchApi("/health", { cache: "no-store" }).catch(() => {}); }, 10 * 60 * 1000);
    return () => { clearInterval(iv); if (hb) clearInterval(hb); };
  }, []);

  const applyTheme = useCallback((d: boolean) => {
    setDark(d);
    document.documentElement.classList.toggle("dark", d);
    localStorage.setItem("theme", d ? "dark" : "light");
  }, []);

  return { st, stLoading, stateErr, setSt, refresh, tick, dark, applyTheme };
}
