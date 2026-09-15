"use client";

import { useState } from "react";
import { Activity, ChevronDown } from "lucide-react";

/* ---------- лента активности: события бота/диспетчера/курьера/системы поверх карты ---------- */

const FEED_META: Record<string, { ico: string; who: string }> = {
  bot: { ico: "🤖", who: "Бот" },
  disp: { ico: "🧭", who: "Диспетчер" },
  cour: { ico: "🚴", who: "Курьер" },
  sys: { ico: "📡", who: "" },
};

export default function ActivityFeed({ events }: { events: { t: number; actor: string; text: string }[] }) {
  const [open, setOpen] = useState(true);
  if (!events.length) return null;
  const rows = [...events].reverse().slice(0, 30);
  return (
    <div className={"feed" + (open ? "" : " min")} role="log" aria-live="polite">
      <button className="feed-head" onClick={() => setOpen(o => !o)}>
        <Activity size={13} /> Лента активности
        <span className="feed-n">{events.length}</span>
        <ChevronDown size={14} className={"feed-chev" + (open ? " open" : "")} />
      </button>
      {open && (
        <div className="feed-list">
          {rows.map((ev, i) => {
            const m = FEED_META[ev.actor] || FEED_META.sys;
            const d = new Date(ev.t * 1000);
            return (
              <div className={"feed-ev a-" + ev.actor} key={ev.t + "-" + i}>
                <span className="feed-ico">{m.ico}</span>
                <span className="feed-txt">{m.who ? <b>{m.who} </b> : null}{ev.text}</span>
                <span className="feed-time">
                  {d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
