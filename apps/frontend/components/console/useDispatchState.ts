"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AppState } from "@/lib/api";

/* ---------- данные консоли: кэш + живые обновления + тикер возраста + тема ----------
   План считается ТОЛЬКО по кнопке «Рассчитать». Живые обновления (несколько
   админов, геолокации курьеров) — long-poll /api/rev: он висит, пока состояние
   не изменится, тогда инвалидируем кэш и каждый браузер перезаказывает
   /api/state сам. */

export function useDispatchState() {
  const qc = useQueryClient();
  const { data: stData, error: stateErr, isPending: stLoading } = useQuery({
    queryKey: ["state"],
    queryFn: () => api<AppState>("/api/state"),
    staleTime: 10000,
    retry: 1,
    refetchOnWindowFocus: "always",
  });

  // rev берём из уже загруженного state — иначе первый опрос с since=0
  // будил нас сразу и дёргал лишний /api/state на каждой загрузке страницы
  const revRef = useRef(0);
  useEffect(() => { if (stData?.rev != null) revRef.current = stData.rev; }, [stData]);
  useEffect(() => {
    let stop = false;
    (async () => {
      while (!stop) {
        try {
          const r = await fetch(`/api/rev?since=${revRef.current}`);
          if (r.status === 401) { location.assign("/login"); return; }
          const d = await r.json();
          if (d.rev > revRef.current) {
            revRef.current = d.rev;
            qc.invalidateQueries({ queryKey: ["state"] });
          }
        } catch { await new Promise(res => setTimeout(res, 3000)); }
      }
    })();
    return () => { stop = true; };
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
      : setInterval(() => { if (!document.hidden) void fetch("/health", { cache: "no-store" }).catch(() => {}); }, 10 * 60 * 1000);
    return () => { clearInterval(iv); if (hb) clearInterval(hb); };
  }, []);

  const applyTheme = useCallback((d: boolean) => {
    setDark(d);
    document.documentElement.classList.toggle("dark", d);
    localStorage.setItem("theme", d ? "dark" : "light");
  }, []);

  return { st, stLoading, stateErr, setSt, refresh, tick, dark, applyTheme };
}
