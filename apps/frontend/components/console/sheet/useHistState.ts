"use client";

import { useState } from "react";
import { api, fetchApi } from "@/lib/api";
import type { CourierDayStats, HistData, WeekStats } from "../format";

export function useHistState(showToast: (msg: string, err?: boolean) => void) {
  const [histDays, setHistDays] = useState("1");
  const [hist, setHist] = useState<HistData>(null);
  const [weekStats, setWeekStats] = useState<WeekStats | null>(null);
  const [courStats, setCourStats] = useState<CourierDayStats>(null);
  const [csvBusy, setCsvBusy] = useState(false);

  const exportCsv = async () => {
    if (csvBusy) return;
    setCsvBusy(true);
    try {
      // качаем через fetch с Bearer-токеном: прямая ссылка <a> в браузере
      // без куков (инкогнито, блок сторонних кук) ловила 401 «Требуется вход»
      const res = await fetchApi("/api/history/export?days=" + histDays, { cache: "no-store" });
      if (!res.ok) throw new Error("Выгрузка не удалась (" + res.status + ")");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "history-" + new Date().toISOString().slice(0, 10) + ".csv";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
    } catch (e) { showToast((e as Error).message, true); }
    finally { setCsvBusy(false); }
  };

  const loadHistory = async (days: string) => {
    try { setHist(await api("/api/history?days=" + days)); }
    catch (e) { showToast((e as Error).message, true); }
  };
  const loadWeek = async () => {
    try { setWeekStats(await api<WeekStats>("/api/stats/week")); }
    catch { setWeekStats(null); }
  };
  const loadCour = async () => {
    try { setCourStats(await api<NonNullable<CourierDayStats>>("/api/stats/couriers")); }
    catch { setCourStats(null); }
  };

  return { histDays, setHistDays, hist, weekStats, courStats, csvBusy, exportCsv, loadHistory, loadWeek, loadCour };
}

export type HistApi = ReturnType<typeof useHistState>;
