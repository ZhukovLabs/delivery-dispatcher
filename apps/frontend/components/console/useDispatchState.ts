"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, fetchApi, type AppState, type CourierPos, type CourierGeo } from "@/lib/api";
import { ensureWsToken, getSocket } from "@/lib/ws";

/* ---------- данные консоли: кэш + живые обновления + тикер возраста + тема ----------
   План считается ТОЛЬКО по кнопке «Рассчитать». Живые обновления (несколько
   админов, геолокации курьеров) — socket.io: сервер при любом изменении
   состояния пушит "state" с payload'ом депо подписчика (см. lib/ws.ts). */

export function useDispatchState(
  /* оптимистичные патчи летящих мутаций: входящий WS-снимок прокатываем
     через них же — снимки «до мутации» не откатывают UI, а чужие события
     (solving, другие админы) доставляются без потерь */
  live?: { current: Map<string, (s: AppState) => AppState> },
) {
  const qc = useQueryClient();
  const lastRev = useRef(-1); // последний серверный rev (REST или WS)
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
      lastRev.current = Math.max(lastRev.current, s.rev ?? 0);
      return s;
    },
    staleTime: 10000,
    retry: 1,
    refetchOnWindowFocus: "always",
  });

  // живые обновления: сервер пушит payload целиком. Рев сравниваем с
  // последним СЕРВЕРНЫМ rev — реплей старого бродкаста не откатит состояние;
  // снимки, прилетевшие во время летящей мутации, обогащаем её патчем
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
        const drev = d.rev ?? 0;
        if (drev <= lastRev.current) return; // дубль или отставший снимок
        lastRev.current = drev;
        qc.setQueryData<AppState>(["state"], (old) => {
          let base: AppState = d;
          if (live?.current?.size) for (const p of live.current.values()) base = p(base);
          return { ...base, me: old?.me, users: old?.users, my_point: old?.my_point };
        });
      };
      // лёгкий тик движения (раз в секунду): патчим только позиции/скорости/
      // оценки курьеров в текущем снимке — полный state за гео не гоняем
      const onGeo = (d: { t: number; couriers: Array<{ id: string; pos?: CourierPos; cur_kmh?: number; geo?: CourierGeo }> }) => {
        if (!Array.isArray(d?.couriers)) return;
        const patch = new Map(d.couriers.map(c => [c.id, c]));
        qc.setQueryData<AppState>(["state"], (old) => {
          if (!old?.couriers) return old;
          return { ...old, couriers: old.couriers.map(c => {
            const g = patch.get(c.id);
            if (!g) return c;
            const nc = { ...c };
            if (g.pos) nc.pos = g.pos; else delete nc.pos;
            if (g.cur_kmh !== undefined) nc.cur_kmh = g.cur_kmh; else delete nc.cur_kmh;
            if (g.geo) nc.geo = g.geo; else delete nc.geo;
            return nc;
          })};
        });
      };
      const onConnect = () => { void qc.invalidateQueries({ queryKey: ["state"] }); }; // подтянуть пропущенное за простой
      s.on("state", onState);
      s.on("geo", onGeo);
      s.on("connect", onConnect);
      if (!s.connected) s.connect();
      off = () => { s.off("state", onState); s.off("geo", onGeo); s.off("connect", onConnect); };
    })();
    return () => { cancelled = true; off?.(); };
  }, [qc, live]);

  const st = stData ?? null;
  const setSt = useCallback((s: AppState) => {
    // две быстрые мутации (например, выдача двум курьерам подряд) летят
    // параллельно: ответ первой может прийти ПОСЛЕ ответа второй —
    // старый rev не откатывает UI (иначе выдача «пропадала» до следующего
    // события сервера)
    const r = s.rev ?? 0;
    if (r < lastRev.current) return;
    lastRev.current = r;
    qc.setQueryData(["state"], s);
  }, [qc]);
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
