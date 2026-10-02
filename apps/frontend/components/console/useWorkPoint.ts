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
  // выбор живёт один день: в 00:00 local storage очищается, утром точка
  // не тянется со вчера (диспетчер утром осознанно выбирает смену)
  const wpDay = () => {
    const d = new Date();
    return d.getFullYear() + "-" + (d.getMonth() + 1) + "-" + d.getDate();
  };
  const wpWipe = () => {
    try {
      localStorage.removeItem("workPoint");
      localStorage.removeItem("workPointDay");
    } catch {}
  };
  const [workPoint, setWorkPoint] = useState(() => {
    if (typeof window === "undefined") return "";
    try {
      if (localStorage.getItem("workPointDay") !== wpDay()) {
        wpWipe(); // вчерашняя смена (или нет даты) — начинаем день заново
        return "";
      }
      return localStorage.getItem("workPoint") || "";
    } catch { return ""; }
  });
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
  useEffect(() => {
    if (!workPoint) return;
    try {
      localStorage.setItem("workPoint", workPoint);
      localStorage.setItem("workPointDay", wpDay());
    } catch {}
  }, [workPoint]);
  // открытый через полночь таб: в 00:00 очищаем хранилище (текущая смена
  // в сессии продолжает работать, утром выбор начнётся с чистого листа)
  useEffect(() => {
    const now = new Date();
    const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
    const id = window.setTimeout(wpWipe, midnight.getTime() - now.getTime());
    return () => window.clearTimeout(id);
  }, []);
  // сообщаем серверу, где мы работаем: при первом входе и при смене селектора
  useEffect(() => {
    if (!workPoint || !st?.points?.length) return;
    if (!st.points!.some(p => p.id === workPoint)) return; // сохранённая точка мертва — коррекция выше подменит id
    if (wpSynced.current || wpSwitchingRef.current) return; // ручная смена уже постит сама
    wpSynced.current = true;
    void api("/api/workpoint", "POST", { point_id: workPoint })
      .catch(() => wpWipe());
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
