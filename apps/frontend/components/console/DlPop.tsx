"use client";

import { useEffect, useRef, useState } from "react";
import { dlRound } from "./format";

/* ---------- попап дедлайна: быстрые +N мин, правка часов/минут ---------- */

export default function DlPop({ initial, hasDeadline, onSave, onClose }: {
  initial: string; hasDeadline: boolean;
  onSave: (val: string) => Promise<void>; onClose: () => void;
}) {
  const [val, setVal] = useState(initial);
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node) &&
        !(e.target as HTMLElement).closest("[title='Обещанное время доставки (дедлайн)']")) onClose();
    };
    document.addEventListener("click", h);
    return () => document.removeEventListener("click", h);
  }, [onClose]);
  const shift = (n: number) => {
    const [h, m] = val.split(":").map(Number);
    setVal(`${String((h + Math.floor((m + n) / 60) + 24) % 24).padStart(2, "0")}:${String((m + n + 60) % 60).padStart(2, "0")}`);
  };
  const setPart = (part: "hh" | "mm", raw: string) => {
    const d = raw.replace(/\D/g, "").slice(0, 2);
    const [h = "00", m = "00"] = val.split(":");
    setVal(part === "hh" ? `${(d || "0").padStart(2, "0")}:${m}` : `${h}:${(d || "0").padStart(2, "0")}`);
  };
  const save = () => {
    let [h, m] = val.split(":").map(Number);
    setVal(`${String(Math.min(23, Math.max(0, h || 0))).padStart(2, "0")}:${String(Math.min(59, Math.max(0, m || 0))).padStart(2, "0")}`);
    void onSave(`${String(Math.min(23, Math.max(0, h || 0))).padStart(2, "0")}:${String(Math.min(59, Math.max(0, m || 0))).padStart(2, "0")}`);
  };
  return (
    <div className="dl-pop" ref={ref} onClick={e => e.stopPropagation()}>
      <div className="dl-row">
        {[15, 30, 45, 60, 90].map(n => (
          <button key={n} className="dl-q" onClick={() => void onSave(dlRound(new Date(Date.now() + n * 60000)))}>
            {n === 60 ? "1 ч" : n === 90 ? "1.5 ч" : "+" + n}
          </button>
        ))}
        <span className="dl-ql">мин от сейчас</span>
      </div>
      <div className="dl-row">
        <button className="dl-tbtn" title="На 5 минут раньше" onClick={() => shift(-5)}>−5</button>
        <input className="dl-hh" maxLength={2} inputMode="numeric" aria-label="Часы" value={val.split(":")[0]}
          onChange={e => setPart("hh", e.target.value)} />
        <span className="dl-colon">:</span>
        <input className="dl-mm" maxLength={2} inputMode="numeric" aria-label="Минуты" value={val.split(":")[1]}
          onChange={e => setPart("mm", e.target.value)} />
        <button className="dl-tbtn" title="На 5 минут позже" onClick={() => shift(5)}>+5</button>
        <span className="dl-flex" />
        <button className="dl-ok" title="Сохранить дедлайн" onClick={save}>✓</button>
        {hasDeadline && <button className="dl-x" title="Убрать дедлайн" onClick={() => void onSave("")}>✕</button>}
      </div>
    </div>
  );
}
