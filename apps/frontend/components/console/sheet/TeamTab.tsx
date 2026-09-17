"use client";

import { ChevronDown, Download, Pencil, Trash2 } from "lucide-react";
import type { AppState } from "@/lib/api";
import { avaOf, initialsOf } from "../format";
import type { TeamApi } from "./useTeamState";

/** Строка «Права администратора» с тумблером — общая для добавления и редактирования диспетчера. */
function RoleSwitch({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="t-role">
      <span className="t-role-l">Права администратора
        <small>Параметры расчёта, участники и точки выдачи</small>
      </span>
      <button type="button" role="switch" aria-checked={on} aria-label="Права администратора"
        className={"pswitch" + (on ? " on" : "")}
        onClick={() => onChange(!on)}><i /></button>
    </div>
  );
}

export default function TeamTab({ st, team }: { st: AppState; team: TeamApi }) {
  const {
    userEmail, setUserEmail, userName, setUserName, userPhone, setUserPhone,
    userPwd, setUserPwd, userIsAdmin, setUserIsAdmin,
    addOpen, setAddOpen, editId, setEditId,
    eMail, setEMail, eName, setEName, ePhone, setEPhone, eAdmin, setEAdmin, ePwd, setEPwd,
    addUser, delUser, updUser, resetPwd,
  } = team;

  return (<>
    <div className="psec">
      <h4>Диспетчеры · {(st.users || []).length}</h4>
      {(st.users || []).map(u => {
        const [abg, afg] = avaOf(u.email);
        const ini = initialsOf(u.name || "", u.email);
        const isSelf = u.id === st.me?.id;
        const editing = editId === u.id;
        const startEdit = () => { setEditId(u.id); setEMail(u.email); setEName(u.name || ""); setEPhone(u.phone || ""); setEAdmin(!!u.is_admin); setEPwd(""); };
        return (
          <div key={u.id}>
            <div className="trow">
              <span className="t-ava" aria-hidden="true" style={{ background: abg, color: afg }}>{ini}</span>
              <span className="t-who">
                <b>{u.name || u.email.split("@")[0]}</b>
                <small>{u.email}{u.phone ? " · " + u.phone : ""}</small>
              </span>
              {!!u.is_admin && <span className="chip chip-green">админ</span>}
              {isSelf && <span className="chip">это вы</span>}
              <button type="button" className={"ticon" + (editing ? " on" : "")} title="Изменить"
                aria-label={"Изменить " + u.email} aria-expanded={editing}
                onClick={() => (editing ? setEditId(null) : startEdit())}><Pencil size={15} /></button>
              <button type="button" className="tdel" disabled={isSelf}
                title={isSelf ? "Себя удалить нельзя" : "Удалить пользователя"}
                aria-label={"Удалить " + u.email}
                onClick={() => void delUser(u.id, u.email)}><Trash2 size={15} /></button>
            </div>
            {editing && (
              <div className="tedit">
                <div className="team-grid">
                  <label className="pf-l">Имя
                    <input type="text" aria-label="Имя пользователя" value={eName}
                      onChange={e => setEName(e.target.value)} /></label>
                  <label className="pf-l">Телефон
                    <input type="tel" placeholder="+375 29 123-45-67" aria-label="Телефон пользователя"
                      inputMode="tel" value={ePhone} onChange={e => setEPhone(e.target.value)} /></label>
                  <label className="pf-l">Email
                    <input type="email" aria-label="Email пользователя" value={eMail}
                      onChange={e => setEMail(e.target.value)} /></label>
                  <label className="pf-l">Новый пароль
                    <input type="password" placeholder="оставьте пустым, чтобы не менять"
                      aria-label="Новый пароль пользователя" value={ePwd}
                      onChange={e => setEPwd(e.target.value)} /></label>
                </div>
                {!isSelf && <RoleSwitch on={eAdmin} onChange={setEAdmin} />}
                <div className="tedit-actions">
                  <button className="btn btn-primary"
                    disabled={!(eMail.trim() && eName.trim().length >= 2 && ePhone.trim())}
                    onClick={async () => {
                      const ok = await updUser(u.id, { email: eMail.trim(), name: eName.trim(), phone: ePhone.trim(), is_admin: isSelf ? true : eAdmin });
                      const pwdOk = !ePwd || await resetPwd(u.id, ePwd);
                      if (ok && pwdOk) setEditId(null);
                    }}>Сохранить</button>
                  <button className="btn" onClick={() => setEditId(null)}>Отмена</button>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>

    <div className={"sacc" + (addOpen ? " open" : "")} style={{ marginTop: 12 }}>
      <button type="button" className="sacc-h" aria-expanded={addOpen}
        onClick={() => setAddOpen(v => !v)}>Добавить диспетчера<ChevronDown size={16} className="chev" /></button>
      <div className="sacc-b"><div className="sacc-c col team-col">
        <div className="team-grid">
          <label className="pf-l">Имя
            <input type="text" placeholder="Иван" aria-label="Имя нового пользователя"
              value={userName} onChange={e => setUserName(e.target.value)} /></label>
          <label className="pf-l">Телефон
            <input type="tel" placeholder="+375 29 123-45-67" aria-label="Телефон нового пользователя"
              inputMode="tel" value={userPhone} onChange={e => setUserPhone(e.target.value)} /></label>
          <label className="pf-l">Email
            <input type="email" placeholder="ivan@cafe.by" aria-label="Email нового пользователя"
              value={userEmail} onChange={e => setUserEmail(e.target.value)} /></label>
          <label className="pf-l">Начальный пароль
            <input type="password" placeholder="минимум 4 символа" aria-label="Начальный пароль"
              value={userPwd} onChange={e => setUserPwd(e.target.value)} /></label>
        </div>
        <RoleSwitch on={userIsAdmin} onChange={setUserIsAdmin} />
        <button className="btn btn-primary" disabled={!(userEmail.trim() && userPwd.length >= 4)}
          onClick={() => void addUser()}>Добавить</button>
      </div></div>
    </div>

    <a href="/api/backup" className="t-back" title="Скачать снимок базы данных">
      <Download size={14} />Бэкап базы
    </a>
  </>);
}
