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
    // пока ответ летел, кэш мог уйти вперёд (WS-бродкаст): не откатываем —
    // дотягиваемся до актуальной версии парой повторов, иначе отдаём кэш
    queryFn: async () => {
      let s = await api<AppState>("/api/state");
      const cur = qc.getQueryData<AppState>(["state"]);
      if (cur?.rev != null && (s.rev ?? 0) < cur.rev) {
        for (let i = 0; i < 2 && (s.rev ?? 0) < cur.rev; i++) {
          await new Promise(r => setTimeout(r, 250));
          s = await api<AppState>("/api/state");
        }
        if ((s.rev ?? 0) < (cur.rev ?? 0)) return cur;
      }
      return s;
    },
    staleTime: 10000,
    retry: 1,
    refetchOnWindowFocus: "always",
  });

  // живые обновления: сервер пушит payload целиком. Применяем ТОЛЬКО более
  // свежую версию (rev строго больше) — медленный REST-ответ или реплей
  // старого бродкаста больше не может откатить состояние назад; пока летит
  // оптимистичный патч (rev локально поднят), бродкасты его не затирают
  useEffect(() => {
    let off: (() => void) | null = null;
    let cancelled = false;
    void (async () => {
      if (!(await ensureWsToken()) || cancelled) return;
      const s = getSocket();
      // payload бродкаста собирается без сессии (me=null, users=[]) —
      // сессионные поля не затираем, иначе админские вкладки гаснут
      // на первом же живом обновлении
      const onState = (d: AppState) => {
        qc.setQueryData<AppState>(["state"], (old) => {
          if (old && (d.rev ?? 0) <= (old.rev ?? 0)) return old;
          return { ...d, me: old?.me, users: old?.users, my_point: old?.my_point };
        });
      };
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
