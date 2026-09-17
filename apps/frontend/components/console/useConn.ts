"use client";

import { useEffect, useRef, useState } from "react";
import { subscribeConn, type WsConnState } from "@/lib/ws";

export function useConn() {
  /* связь с сервером: индикатор в шапке + поведение оверлея расчёта */
  const [conn, setConn] = useState<WsConnState>("connecting");
  useEffect(() => subscribeConn(setConn), []);
  const prevConn = useRef<WsConnState>("connecting");

  /* блокировка на время расчёта не должна быть вечной: пропала связь —
     через минуту снимаем оверлей сами (иначе мёртвый WS навсегда «морозит»
     консоль последним состоянием solving=true) */
  const [solveHide, setSolveHide] = useState(false);
  useEffect(() => {
    if (conn !== "offline") {
      setSolveHide(false); // связь есть — блокировка честная, таймер не нужен
      return;
    }
    const t = setTimeout(() => setSolveHide(true), 60_000);
    return () => clearTimeout(t);
  }, [conn]);

  return { conn, prevConn, solveHide };
}
