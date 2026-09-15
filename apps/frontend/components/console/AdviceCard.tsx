"use client";

import { Check, Clock, Paperclip, Scale, Users } from "lucide-react";
import type { Advice } from "@/lib/api";
import { declName, shortAddr } from "./format";

/* ---------- совет «ждать/не ждать» возвращающегося курьера ---------- */

export default function AdviceCard({ a, busy, onMode }: { a: Advice; busy: boolean; onMode: (m: string) => void; }) {
  const nm = declName(a.wait_couriers.map(w => w.name).join(", "));
  const lastBack = a.wait_couriers.map(w => w.back_clock).sort().pop();
  const backTxt = a.wait_couriers.length === 1 ? `вернётся ≈${lastBack}` : `до ≈${lastBack}`;
  const delta = a.gain_last_min | 0;
  const rel = a.chosen === "split" ? -delta : delta; // >0 — выбранный сценарий хуже второго
  const badge = rel === 0 ? null : (
    <span className={"adv-badge " + (rel < 0 ? "ok" : "no")}
      title="Разница выбранного сценария по времени последней доставки">
      {rel < 0 ? `−${Math.abs(rel)}` : `+${rel}`} мин
    </span>
  );
  const list = a.held.map(h => shortAddr(h.address));
  const row = (id: string, title: string, side: { counts: string; last_clock?: string; avg_min: number }) => (
    <button className={"adv-opt" + (a.chosen === id ? " chosen" : "")} title="Применить этот сценарий" onClick={() => onMode(id)}>
      <span className="adv-opt-t">
        <span className="adv-dot" aria-hidden="true" />
        {title}
        {a.chosen === id && <Check size={13} className="adv-done" />}
        {a.chosen === id && badge}
      </span>
      <span className="adv-opt-meta"><Users size={11} />{side.counts}</span>
      <span className="adv-opt-nums"><Clock size={11} />≈{side.last_clock || "?"} · ср {side.avg_min} мин</span>
    </button>
  );
  return (
    <div className={"advice" + (busy ? " busy" : "")} title="Сравнение сценариев: всё курьерам на базе сейчас или разделить с возвращающимся">
      <div className="adv-head">
        <Scale size={14} />
        <span className="adv-q">Ждать {nm}?</span>
        <span className="adv-back">{backTxt}</span>
      </div>
      <div className="adv-opts">
        {row("now", "Не ждать", a.now)}
        {row("split", "Ждать", a.split)}
      </div>
      {list.length > 0 && (
        <div className="adv-held" title={list.join("\n")}>
          <Paperclip size={11} />
          <span>{declName(a.held[0].courier)}: {list.slice(0, 3).join(" · ")}{list.length > 3 ? ` …ещё ${list.length - 3}` : ""}</span>
        </div>
      )}
    </div>
  );
}
