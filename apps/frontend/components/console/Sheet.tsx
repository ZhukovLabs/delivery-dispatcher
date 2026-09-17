"use client";

import { useEffect } from "react";
import { ChartColumn, SlidersHorizontal, User, Users } from "lucide-react";
import { api, type AppState } from "@/lib/api";
import { useBackdropClose } from "./useBackdropClose";
import ParamsTab from "./sheet/ParamsTab";
import HistTab from "./sheet/HistTab";
import ProfTab from "./sheet/ProfTab";
import TeamTab from "./sheet/TeamTab";
import { useHistState } from "./sheet/useHistState";
import { useProfState } from "./sheet/useProfState";
import { useTeamState } from "./sheet/useTeamState";

/* ---------- «Ещё»: профиль / статистика / параметры расчёта / участники ---------- */

export default function Sheet({ st, tab, setTab, onClose, setSt, showToast, askConfirm }: {
  st: AppState;
  tab: "params" | "hist" | "prof" | "team";
  setTab: (t: "params" | "hist" | "prof" | "team") => void;
  onClose: () => void;
  setSt: (s: AppState) => void;
  showToast: (msg: string, err?: boolean) => void;
  askConfirm: (text: string, opts?: { ok?: string; danger?: boolean }) => Promise<boolean>;
}) {
  const s = st.settings;
  const hist = useHistState(showToast);
  const prof = useProfState(st, setSt, showToast);
  const team = useTeamState(st, setSt, showToast, askConfirm);

  useEffect(() => { if (tab === "hist") { void hist.loadHistory(hist.histDays); void hist.loadWeek(); void hist.loadCour(); } }, [tab]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (tab === "prof") { prof.setProfName(st.me?.name || ""); prof.setProfPhone(st.me?.phone || ""); } }, [tab]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = async (patch: Record<string, number | boolean>) => {
    const full: Record<string, number | boolean> = {
      speed_kmh: s.speed_kmh, handover_min: s.handover_min, max_orders: s.max_orders,
      traffic: s.traffic, lights_sec_per_km: s.lights_sec_per_km,
      auto_prio_min: s.auto_prio_min, reload_min: s.reload_min,
      approach_center_min: s.approach_center_min ?? 4, approach_far_min: s.approach_far_min ?? 2,
      hour_traffic: s.hour_traffic ? 1 : 0,
      ...patch,
    };
    try {
      setSt(await api<AppState>("/api/settings", "POST", full));
      showToast("Параметры сохранены");
    } catch (e) { showToast((e as Error).message, true); }
  };

  const TITLES = { params: "Параметры расчёта", hist: "Статистика", prof: "Профиль", team: "Участники" } as const;

  const bg = useBackdropClose(onClose);
  return (
    <div className="sheet-bg" {...bg}>
      <div className="sheet" aria-label={TITLES[tab]}>
        <button className="close" aria-label="Закрыть" onClick={onClose}>✕</button>
        <div className="sheet-tabs" role="tablist">
          <button role="tab" aria-selected={tab === "prof"} className={tab === "prof" ? "on" : ""}
            onClick={() => setTab("prof")}><User size={14} />Профиль</button>
          <button role="tab" aria-selected={tab === "hist"} className={tab === "hist" ? "on" : ""}
            onClick={() => setTab("hist")}><ChartColumn size={14} />Статистика</button>
          {!!st.me?.is_admin && (
            <button role="tab" aria-selected={tab === "params"} className={tab === "params" ? "on" : ""}
              onClick={() => setTab("params")}><SlidersHorizontal size={14} />Параметры расчёта</button>
          )}
          {!!st.me?.is_admin && (
            <button role="tab" aria-selected={tab === "team"} className={tab === "team" ? "on" : ""}
              onClick={() => setTab("team")}><Users size={14} />Участники</button>
          )}
        </div>

        {tab === "params" && !!st.me?.is_admin && <ParamsTab s={s} set={set} />}

        {tab === "hist" && <HistTab api={hist} />}

        {tab === "prof" && <ProfTab st={st} prof={prof} />}

        {tab === "team" && !!st.me?.is_admin && <TeamTab st={st} team={team} />}
      </div>
    </div>
  );
}
