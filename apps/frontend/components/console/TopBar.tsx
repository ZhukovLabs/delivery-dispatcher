"use client";

import { useEffect, useState } from "react";
import { Check, CircleHelp, LogOut, MapPin, Moon, Route as RouteIcon, Settings, Sun, Timer, Undo2, User, X } from "lucide-react";
import type { AppState } from "@/lib/api";
import { dropSocket, subscribeConn, type WsConnState } from "@/lib/ws";

/* ---------- шапка: бренд, выбор места работы, счётчики дня, отмена/тема/справка/профиль ---------- */

export default function TopBar({ st, workPoint, firstPid, onWorkPoint,
  undoLen, lastLabel, onUndo, dark, onTheme, onHelp, onProfile,
}: {
  st: AppState;
  workPoint: string;
  firstPid: string;
  onWorkPoint: (pid: string) => void;
  undoLen: number;
  lastLabel: string;
  onUndo: () => void;
  dark: boolean;
  onTheme: (d: boolean) => void;
  onHelp: () => void;
  onProfile: () => void;
}) {
  const me: NonNullable<AppState["me"]> = st.me || { id: "", email: "" };
  const today: NonNullable<AppState["today"]> = st.today || {};
  const [conn, setConn] = useState<WsConnState>("connecting");
  useEffect(() => subscribeConn(setConn), []);
  const CONN_META: Record<WsConnState, { cls: string; title: string; label: string }> = {
    online: { cls: "ws-on", title: "Живая связь с сервером установлена", label: "онлайн" },
    connecting: { cls: "ws-wait", title: "Подключаемся к серверу…", label: "связь…" },
    offline: { cls: "ws-off", title: "Нет связи с сервером — переподключаемся", label: "нет связи" },
  };
  const cm = CONN_META[conn];
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark"><RouteIcon size={16} strokeWidth={2.25} /></span>
        <span className="brand-name">Диспетчер доставки</span>
      </div>
      <span className={`wsdot ${cm.cls}`} role="status" aria-live="polite" title={cm.title}>
        <span className="wsdot-dot" aria-hidden="true" />
        {cm.label}
      </span>
      {(st.points?.length || 0) > 0 && (
        <label className="pp-hsel" title="Точка выдачи, в которой вы работаете: новые заказы попадают к курьерам этой точки">
          <MapPin size={13} />
          <span className="pp-hsel-cap">Место работы:</span>
          <select value={workPoint || firstPid} aria-label="Рабочая точка выдачи"
            onChange={e => onWorkPoint(e.target.value)}>
            {st.points!.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
      )}
      {(!!today.delivered || !!today.cancelled || !!today.avg_cycle_min) && (
        <div className="daystats">
          <span className="dchip ok" title="Доставлено сегодня"><Check size={12} strokeWidth={2.5} />{today.delivered || 0}</span>
          {!!today.cancelled && (
            <span className="dchip no" title="Отменено сегодня"><X size={12} strokeWidth={2.5} />{today.cancelled}</span>
          )}
          {!!today.avg_cycle_min && (
            <span className="dchip" title="Средний цикл заказа"><Timer size={12} />{today.avg_cycle_min} мин</span>
          )}
        </div>
      )}
      <div className="spacer" />
      {undoLen > 0 && (
        <button id="undoBtn" className="iconbtn" style={{ display: "" }}
          title={`Отменить: ${lastLabel} (Ctrl+Z)`}
          aria-label="Отменить действие"
          onClick={onUndo}><Undo2 size={16} /></button>
      )}
      <button className="iconbtn" title="Тёмная тема" aria-label="Переключить тему"
        onClick={() => onTheme(!dark)}>{dark ? <Sun size={16} /> : <Moon size={16} />}</button>
      <button className="iconbtn" title="Как пользоваться" aria-label="Справка"
        onClick={onHelp}><CircleHelp size={16} /></button>
      <button className="iconbtn gear" title="Профиль" aria-label="Профиль"
        onClick={onProfile}><Settings size={16} /></button>
      <span className="vdiv" />
      <span className="me" title={me.email || ""}>
        <User size={13} />
        {me.email ? `${me.email}${me.is_admin ? " · админ" : ""}` : ""}
      </span>
      <button id="logoutLink" title="Выйти" aria-label="Выйти" onClick={async () => {
        dropSocket(); // WS-подписку рвём сразу — сессии больше нет
        await fetch("/api/logout", { headers: { Accept: "application/json" } });
        window.location.href = "/login";
      }}><LogOut size={15} /></button>
    </header>
  );
}
