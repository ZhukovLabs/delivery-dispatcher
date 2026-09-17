"use client";

import { useEffect, useRef, useState } from "react";
import { api, type AppState } from "@/lib/api";
import { joinDepot } from "@/lib/ws";

export function useWorkPoint(deps: {
  st: AppState | null;
  refresh: () => Promise<void>;
  clearHighlights: () => void;
  refit: () => void;
}) {
  const { st, refresh, clearHighlights, refit } = deps;
  /* ---------- место работы администратора ---------- */
  const [workPoint, setWorkPoint] = useState(() => (typeof window !== "undefined" ? localStorage.getItem("workPoint") || "" : ""));
  const firstPid = st?.points?.[0]?.id || "";
  const wpSynced = useRef(false);
  const [wpSwitching, setWpSwitching] = useState(false);
  const wpSwitchingRef = useRef(false);
  useEffect(() => {
    if (!st?.points?.length) return;
    setWorkPoint(cur => {
      if (st.points!.some(p => p.id === cur)) return cur;
      return st.points![0].id;
    });
  }, [st?.points]);
  useEffect(() => { if (workPoint) localStorage.setItem("workPoint", workPoint); }, [workPoint]);
  // сообщаем серверу, где мы работаем: при первом входе и при смене селектора
  useEffect(() => {
    if (!workPoint || !st?.points?.length) return;
    if (!st.points!.some(p => p.id === workPoint)) return; // сохранённая точка мертва — коррекция выше подменит id
    if (wpSynced.current || wpSwitchingRef.current) return; // ручная смена уже постит сама
    wpSynced.current = true;
    void api("/api/workpoint", "POST", { point_id: workPoint })
      .catch(() => { try { localStorage.removeItem("workPoint"); } catch {} });
  }, [workPoint, st?.points]);
  const onWorkPoint = (pid: string) => {
    if (pid === workPoint || wpSwitchingRef.current) return;
    wpSwitchingRef.current = true;
    setWpSwitching(true);
    setWorkPoint(pid);
    clearHighlights();   // подсветка/балун старого депо больше не актуальны
    void (async () => {
      try {
        await api("/api/workpoint", "POST", { point_id: pid });
        joinDepot(pid); // WS: переехать в руму нового депо (сервер пушнет его состояние)
      } catch {
        wpSynced.current = false; // точка могла стать мёртвой — эффект коррекции подменит id и повторит
      }
      // заказы, план и счётчики приходят из /api/state уже для новой точки —
      // без рефетча UI показывал бы старое депо (#1/#7)
      await refresh();
      wpSwitchingRef.current = false;
      setWpSwitching(false);
      refit(); // пересобрать кадр карты под новое депо
    })();
  };
  return { workPoint, firstPid, wpSwitching, onWorkPoint };
}
