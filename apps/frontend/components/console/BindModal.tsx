"use client";

import { useState } from "react";
import { ArrowRight, Check, Send, Unlink, X } from "lucide-react";
import { api, fmtAge, type AppState, type Courier } from "@/lib/api";

/* ---------- модалка привязки Telegram: список видевших бота, ручной ID, сообщение/отвязка ---------- */

export default function BindModal({ courier, bot, seen, onDone, onClose }: {
  courier: Courier; bot: string;
  seen: { chat_id: string; login: string; ts: number }[];
  onDone: (s: AppState) => void; onClose: () => void;
}) {
  const [manual, setManual] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [sent, setSent] = useState(false);

  const send = async () => {
    setBusy(true); setErr("");
    try {
      const r = await api<{ ok?: boolean }>("/api/notify/tg", "POST",
        { chat_id: courier.tg_chat_id, text: msg.trim() });
      if (r.ok) { setMsg(""); setSent(true); setTimeout(onClose, 900); }
    } catch (e) {
      setErr(String((e as Error).message || e));
    } finally { setBusy(false); }
  };

  const bind = async (chat_id: string, login?: string) => {
    if (!/^\d+$/.test(chat_id)) { setErr("ID должен быть числом"); return; }
    setBusy(true); setErr("");
    try {
      const s = await api<AppState>(`/api/couriers/${courier.id}/bind`, "POST", { chat_id, login });
      onDone(s);
    } catch (e) {
      setErr(String((e as Error).message || e));
    } finally { setBusy(false); }
  };
  const unbind = async () => {
    setBusy(true);
    try { onDone(await api<AppState>(`/api/couriers/${courier.id}/unbind`, "POST")); }
    finally { setBusy(false); }
  };

  return (
    <div className="help-overlay" role="dialog" aria-modal="true"
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="help-card bind-card">
        <button className="bind-close" aria-label="Закрыть" onClick={onClose}><X size={16} /></button>
        <div className="bind-head">
          <span className="bind-ico"><Send size={15} /></span>
          <div>
            <h3 style={{ margin: 0 }}>Telegram</h3>
            <span className="bind-sub">{courier.name}</span>
          </div>
          {courier.tg_chat_id
            ? <span className="bind-chip ok" title={`ID ${courier.tg_chat_id}`}><Check size={11} /> {courier.tg_login ? "@" + courier.tg_login : "ID " + courier.tg_chat_id}</span>
            : <span className="bind-chip">не привязан</span>}
        </div>

        {courier.tg_chat_id ? (<>
          <div className="bind-msg">
            <div className="bind-manual">
              <input name="tg_message" placeholder={`Сообщение для ${courier.tg_login ? "@" + courier.tg_login : "ID " + courier.tg_chat_id}`}
                value={msg} onChange={e => setMsg(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter" && msg.trim() && !busy) void send(); }} />
              <button className="btn btn-primary" disabled={busy || !msg.trim()} onClick={() => void send()}>
                <Send size={13} /> Отправить
              </button>
            </div>
            <small className="bind-msg-note">{sent ? <><Check size={11} /> отправлено</> : "Сообщение придёт от имени бота в личный чат"}</small>
          </div>
          {err && <p className="bind-err" role="alert">{err}</p>}
          <button className="bind-unlink" disabled={busy} onClick={() => void unbind()}><Unlink size={13} /> Отвязать Telegram</button>
        </>) : (<>
          <div className="bind-steps">
            <span className="bind-step"><i>1</i><span>Курьер открывает бота{" "}
              {bot ? <a className="bind-bot" href={`https://t.me/${bot.replace(/^@/, "")}`}
                target="_blank" rel="noreferrer">{bot}</a> : <b>развозки</b>} и&nbsp;нажимает&nbsp;«Запустить»</span></span>
            <span className="bind-step"><i>2</i><span>Он появится в списке ниже — нажмите на него</span></span>
          </div>

          {seen.length > 0 && (
            <div className="bind-list">
              {seen.map(u => (
                <button key={u.chat_id} disabled={busy} className="bind-user"
                  title="Привязать этого пользователя к курьеру"
                  onClick={() => void bind(u.chat_id, u.login)}>
                  <span className="bind-user-l">
                    <b>@{u.login}</b>
                    <small>ID {u.chat_id} · {fmtAge(u.ts)} назад</small>
                  </span>
                  <ArrowRight size={14} />
                </button>
              ))}
            </div>
          )}

          <div className="bind-divider">Курьер уже писал боту? Введите его ID</div>
          <div className="bind-manual">
            <input name="tg_chat_id" placeholder="ID из сообщения бота" value={manual} inputMode="numeric"
              onChange={e => setManual(e.target.value.replace(/\D/g, ""))} />
            <button className="btn btn-primary" disabled={busy || !manual} onClick={() => void bind(manual)}>Привязать</button>
          </div>
          {err && <p className="bind-err" role="alert">{err}</p>}
        </>)}
      </div>
    </div>
  );
}
