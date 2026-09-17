"use client";

import { ChevronDown } from "lucide-react";
import type { AppState } from "@/lib/api";
import type { ProfApi } from "./useProfState";

export default function ProfTab({ st, prof }: { st: AppState; prof: ProfApi }) {
  const {
    profName, setProfName, profPhone, setProfPhone,
    pwOld, setPwOld, pwNew, setPwNew,
    profOpen, setProfOpen, pwOpen, setPwOpen,
    saveProfile, changePw,
  } = prof;

  return (<>
    <div className={"sacc" + (profOpen ? " open" : "")}>
      <button type="button" className="sacc-h" aria-expanded={profOpen}
        onClick={() => setProfOpen(v => !v)}>Подпись курьерам<ChevronDown size={16} className="chev" /></button>
      <div className="sacc-b"><div className={"sacc-c col prof-col" + (st.me && !(st.me.name && st.me.phone) ? " need" : "")}>
        <label className="pf-note">Имя и телефон автоматически добавляются под каждым сообщением</label>
        <div className="prof-grid">
          <label className="pf-l">Имя
            <input type="text" placeholder="Настя" aria-label="Ваше имя" value={profName}
              onChange={e => setProfName(e.target.value)} /></label>
          <label className="pf-l">Телефон
            <input type="tel" placeholder="+375 29 123-45-67" aria-label="Ваш телефон" value={profPhone}
              inputMode="tel" onChange={e => setProfPhone(e.target.value)} /></label>
        </div>
        <div className="prof-sign" aria-live="polite">
          <small>Так придёт курьеру:</small>
          <div className="prof-sign-bubble">
            Есть вопросы? - {profName.trim() || <i>имя</i>}, {profPhone.trim() || <i>телефон</i>}
          </div>
        </div>
        <div className="prof-actions">
          <button className="btn btn-primary" disabled={!(profName.trim() && profPhone.trim())}
            onClick={() => void saveProfile()}>Сохранить</button>
          {st.me && !(st.me.name && st.me.phone) && (
            <small className="prof-warn">Пока без подписи — отправка сообщений курьерам недоступна</small>
          )}
         </div>
      </div></div>
    </div>

    <div className={"sacc" + (pwOpen ? " open" : "")}>
      <button type="button" className="sacc-h" aria-expanded={pwOpen}
        onClick={() => setPwOpen(v => !v)}>Смена пароля<ChevronDown size={16} className="chev" /></button>
      <div className="sacc-b"><div className="sacc-c col">
        <div className="prof-grid">
          <label className="pf-l">Старый пароль
            <input type="password" placeholder="••••" aria-label="Старый пароль"
              value={pwOld} onChange={e => setPwOld(e.target.value)} /></label>
          <label className="pf-l">Новый пароль
            <input type="password" placeholder="минимум 4 символа" aria-label="Новый пароль"
              value={pwNew} onChange={e => setPwNew(e.target.value)} /></label>
        </div>
        <button className="btn" disabled={!(pwOld && pwNew.length >= 4)}
          onClick={() => void changePw()}>Сменить пароль</button>
      </div></div>
    </div>
  </>);
}
