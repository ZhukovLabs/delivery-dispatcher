"use client";

import { useEffect, useState } from "react";
import { Bike, ChartColumn, ChevronDown, CircleHelp, Download, FileSpreadsheet, FileText, Pencil, SlidersHorizontal, Trash2, User, Users } from "lucide-react";
import { api, type AppState } from "@/lib/api";
import { avaOf, initialsOf, type HistData, type WeekStats } from "./format";

/* ---------- «Ещё»: профиль / статистика / параметры расчёта / участники ---------- */

const SET_SECTIONS: { id: string; title: string }[] = [
  { id: "move", title: "Время в пути" },
  { id: "addr", title: "У адреса" },
  { id: "trip", title: "Заезды и приоритет" },
];
const SET_FIELDS: { key: string; label: string; unit?: string; min: number; max: number; step?: number; sec: string; tip: string }[] = [
  { key: "speed_kmh", label: "Скорость", unit: "км/ч", min: 5, max: 120, sec: "move",
    tip: "Средняя скорость курьера между адресами. Это запасной расчёт на случай, когда дорожная матрица не ответила. В расчёте: время пути = расстояние ÷ эта скорость." },
  { key: "traffic", label: "Пробки", unit: "коэф.", min: 1, max: 3, step: 0.05, sec: "move",
    tip: "Общая надбавка к дорожному времени: 1 — свободно, 1.25 — обычный день, 1.5–2 — час пик. В расчёте: каждое время в пути из матрицы умножается на этот коэффициент." },
  { key: "lights_sec_per_km", label: "Светофоры", unit: "с/км", min: 0, max: 60, sec: "move",
    tip: "Средняя задержка на светофорах и перекрёстках. В расчёте: секунды добавляются к каждому километру пути; 15 с/км — это примерно +20–25% городского времени." },
  { key: "handover_min", label: "Вручение", unit: "мин", min: 0, max: 60, sec: "addr",
    tip: "Само вручение: позвонить, дождаться клиента, отдать заказ. В расчёте: добавляется к каждому адресу и сдвигает все последующие времена маршрута." },
  { key: "approach_center_min", label: "Подъезд: центр", unit: "мин", min: 0, max: 15, sec: "addr",
    tip: "Запас на парковку и путь до двери клиента для адресов ближе 2.5 км от точки выдачи. В расчёте: фиксированная добавка к каждому такому адресу." },
  { key: "approach_far_min", label: "Подъезд: окраины", unit: "мин", min: 0, max: 15, sec: "addr",
    tip: "То же для адресов дальше 2.5 км. Обычно меньше: на окраинах проще припарковаться. В расчёте: добавка к каждому дальнему адресу." },
  { key: "max_orders", label: "Заказов в заезде", unit: "шт", min: 1, max: 50, sec: "trip",
    tip: "Сколько заказов курьер уносит за один выезд — объём сумки. В расчёте: после этого числа курьер возвращается на точку, и начинается новый заезд." },
  { key: "reload_min", label: "Перезагрузка", unit: "мин", min: 0, max: 120, sec: "trip",
    tip: "Время на точке между заездами: сдать выполненное, принять новую партию, погрузиться. В расчёте: старт следующего заезда = финиш предыдущего + это время." },
  { key: "auto_prio_min", label: "Авто-приоритет", unit: "мин", min: 0, max: 240, sec: "trip",
    tip: "Заказ ждёт в очереди дольше этого времени — сам становится приоритетным. 0 — выключено. В расчёте: возраст заказа повышает его вес, решатель ставит его в маршрут раньше." },
];
const HOUR_TRAFFIC_TIP = "Пробки не постоянны: утром и вечером дороги медленнее, днём свободнее. В расчёте: коэффициент пробок берётся по часу выезда, а не один на весь день.";
const TIP_W = 290; // ширина .ptip-pop из globals.css

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
  const [histDays, setHistDays] = useState("1");
  const [hist, setHist] = useState<HistData>(null);
  const [weekStats, setWeekStats] = useState<WeekStats | null>(null);
  const [pwOld, setPwOld] = useState("");
  const [pwNew, setPwNew] = useState("");
  const [userEmail, setUserEmail] = useState("");
  const [userName, setUserName] = useState("");
  const [userPhone, setUserPhone] = useState("");
  const [profName, setProfName] = useState("");
  const [profPhone, setProfPhone] = useState("");
  const [userPwd, setUserPwd] = useState("");
  const [userIsAdmin, setUserIsAdmin] = useState(false);

  const loadHistory = async (days: string) => {
    try { setHist(await api("/api/history?days=" + days)); }
    catch (e) { showToast((e as Error).message, true); }
  };
  const loadWeek = async () => {
    try { setWeekStats(await api<WeekStats>("/api/stats/week")); }
    catch { setWeekStats(null); }
  };
  useEffect(() => { if (tab === "hist") { void loadHistory(histDays); void loadWeek(); } }, [tab]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (tab === "prof") { setProfName(st.me?.name || ""); setProfPhone(st.me?.phone || ""); } }, [tab]); // eslint-disable-line react-hooks/exhaustive-deps

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

  const summ = hist?.summary || {};
  const WD = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
  const _now0 = new Date();
  const wkCols = Array.from({ length: 7 }, (_, i) => {
    const dt = new Date(_now0.getFullYear(), _now0.getMonth(), _now0.getDate() - (6 - i));
    const day = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
    const rec = weekStats?.days.find(d => d.day === day);
    return { day, date: day, wd: WD[dt.getDay()], delivered: rec?.delivered || 0,
      cancelled: rec?.cancelled || 0, avg_cycle_min: rec?.avg_cycle_min ?? null };
  });
  const wkMax = Math.max(1, ...wkCols.map(d => d.delivered));
  const wkTotal = wkCols.reduce((a, d) => a + d.delivered, 0);
  const wkCour = weekStats?.couriers || [];
  const [profOpen, setProfOpen] = useState(true);
  const [addOpen, setAddOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [eMail, setEMail] = useState(""); const [eName, setEName] = useState("");
  const [ePhone, setEPhone] = useState(""); const [eAdmin, setEAdmin] = useState(false);
  const [ePwd, setEPwd] = useState("");
  const [pwOpen, setPwOpen] = useState(false);
  type TipState = { f: { key: string; label: string; tip: string }; left: number; top: number; below: boolean };
  const [tip, setTip] = useState<TipState | null>(null);
  const showTip = (f: TipState["f"], el: HTMLElement) => {
    const r = el.getBoundingClientRect();
    const below = r.top < 420; // над кнопкой места может не быть — показываем снизу
    setTip({
      f,
      left: Math.max(12, Math.min(r.left - 14, window.innerWidth - (TIP_W + 16))),
      top: below ? r.bottom + 7 : r.top - 8,
      below,
    });
  };
  const hideTip = () => setTip(null);
  const TITLES = { params: "Параметры расчёта", hist: "Статистика", prof: "Профиль", team: "Участники" } as const;

  const saveProfile = async () => {
    try {
      setSt(await api<AppState>("/api/profile", "POST", { name: profName, phone: profPhone }));
      showToast("Профиль сохранён");
    } catch (e) { showToast((e as Error).message, true); }
  };
  const changePw = async () => {
    try {
      await api("/api/password", "POST", { old: pwOld, new: pwNew });
      setPwOld(""); setPwNew("");
      showToast("Пароль изменён");
    } catch (e) { showToast((e as Error).message, true); }
  };
  const addUser = async () => {
    try {
      const nst = await api<AppState>("/api/users", "POST", { email: userEmail, password: userPwd, is_admin: userIsAdmin, name: userName, phone: userPhone });
      setSt(nst);
      setUserEmail(""); setUserPwd(""); setUserIsAdmin(false); setUserName(""); setUserPhone("");
      showToast("Пользователь добавлен");
    } catch (e) { showToast((e as Error).message, true); }
  };
  const delUser = async (uid: string, email: string) => {
    if (!(await askConfirm(`Удалить пользователя «${email}»?`, { ok: "Удалить", danger: true }))) return;
    try { setSt(await api<AppState>("/api/users/" + uid, "DELETE")); }
    catch (e) { showToast((e as Error).message, true); }
  };
  const updUser = async (uid: string, data: { email: string; name: string; phone: string; is_admin: boolean }): Promise<boolean> => {
    try {
      setSt(await api<AppState>("/api/users/" + uid, "PUT", data));
      showToast("Изменения сохранены");
      return true;
    } catch (e) { showToast((e as Error).message, true); return false; }
  };
  const resetPwd = async (uid: string, newPwd: string): Promise<boolean> => {
    try {
      await api("/api/users/" + uid + "/password", "PUT", { new: newPwd });
      showToast("Пароль обновлён");
      return true;
    } catch (e) { showToast((e as Error).message, true); return false; }
  };

  return (
    <div className="sheet-bg" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
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

        {tab === "params" && !!st.me?.is_admin && (<>
          {SET_SECTIONS.map(sec => (
            <div className="psec" key={sec.id}>
              <h4>{sec.title}</h4>
              {SET_FIELDS.filter(f => f.sec === sec.id).map(f => (
                <div className="prow" key={f.key}>
                  <span className="plabel">{f.label}
                    <button type="button" className="ptip" aria-label={"Подсказка: " + f.label}
                      onMouseEnter={e => showTip(f, e.currentTarget)} onMouseLeave={hideTip}
                      onFocus={e => showTip(f, e.currentTarget)} onBlur={hideTip}
                      onClick={e => { e.preventDefault(); showTip(f, e.currentTarget); }}>
                      <CircleHelp size={14} /></button>
                  </span>
                  <span className="pval">
                    <span className="pfield">
                      <input type="number" min={f.min} max={f.max} step={f.step || 1}
                        defaultValue={s[f.key] as number}
                        key={f.key + String(s[f.key])}
                        onChange={e => void set({ [f.key]: +e.target.value })} />
                      <i className="punit">{f.unit}</i>
                    </span>
                  </span>
                </div>
              ))}
              {sec.id === "move" && (
                <div className="prow">
                  <span className="plabel">Почасовые пробки
                    <button type="button" className="ptip" aria-label="Подсказка: почасовые пробки"
                      onMouseEnter={e => showTip({ key: "hour_traffic", label: "Почасовые пробки", tip: HOUR_TRAFFIC_TIP }, e.currentTarget)} onMouseLeave={hideTip}
                      onFocus={e => showTip({ key: "hour_traffic", label: "Почасовые пробки", tip: HOUR_TRAFFIC_TIP }, e.currentTarget)} onBlur={hideTip}
                      onClick={e => { e.preventDefault(); showTip({ key: "hour_traffic", label: "Почасовые пробки", tip: HOUR_TRAFFIC_TIP }, e.currentTarget); }}>
                      <CircleHelp size={14} /></button>
                  </span>
                  <span className="pval">
                    <button type="button" role="switch" aria-checked={!!s.hour_traffic} aria-label="Почасовые пробки"
                      className={"pswitch" + (s.hour_traffic ? " on" : "")}
                      onClick={() => void set({ hour_traffic: s.hour_traffic ? 0 : 1 })}><i /></button>
                  </span>
                </div>
              )}
            </div>
          ))}
          {tip && (
            <div className={"ptip-pop" + (tip.below ? " below" : "")} role="tooltip" style={{ left: tip.left, top: tip.top }}>
              <b>{tip.f.label}.</b> {tip.f.tip}
            </div>
          )}
        </>)}

        {tab === "hist" && (<>
        <div className="stat-cards">
          <div className="scard"><small>Выдано</small><b>{summ.delivered || 0}</b></div>
          <div className="scard"><small>Отменено</small><b>{summ.cancelled || 0}</b></div>
          <div className="scard"><small>Средний цикл</small><b>{summ.avg_cycle_min != null ? summ.avg_cycle_min : "–"}{summ.avg_cycle_min != null && <i>мин</i>}</b></div>
          <div className="scard" title="Заказы, выданные не позже обещанного времени, за 7 дней">
            <small>Вовремя · 7 дней</small>
            <b>{weekStats?.on_time_total ? weekStats.on_time + " из " + weekStats.on_time_total : "–"}</b>
          </div>
          {(!!summ.pay_cash || !!summ.pay_card) && (
            <div className="scard" title="Оплаты, собранные ботом у курьеров после подтверждения доставки">
              <small>Оплаты · наличные / карта</small>
              <b>
                {summ.pay_cash || 0}{summ.pay_cash_sum ? <i title="Сумма наличными">{summ.pay_cash_sum}</i> : null}
                <i className="sep" />
                {summ.pay_card || 0}{summ.pay_card_sum ? <i title="Сумма картой">{summ.pay_card_sum}</i> : null}
              </b>
            </div>
          )}
        </div>

        <h4>Выдачи за 7 дней</h4>
        <div className="wk-chart" role="img" aria-label="Выдачи по дням за неделю">
          {wkCols.map(d => (
            <div className={"wk-col" + (d.delivered ? "" : " z")} key={d.day}
              title={`${d.date}: выдано ${d.delivered}${d.cancelled ? `, отменено ${d.cancelled}` : ""}${d.avg_cycle_min != null ? `, цикл ${d.avg_cycle_min} мин` : ""}`}>
              <span className="wk-num">{d.delivered || ""}</span>
              <span className="wk-bar"><i style={d.delivered ? { height: Math.max(8, Math.round(d.delivered / wkMax * 100)) + "%" } : undefined} /></span>
              <span className="wk-day">{d.wd}</span>
              <span className="wk-date">{d.date.slice(8, 10)}.{d.date.slice(5, 7)}</span>
            </div>
          ))}
        </div>
        {wkTotal === 0 && <div className="empty-list">На этой неделе пока нет закрытых заказов</div>}

        {wkCour.length > 0 && (<>
        <h4>Курьеры за неделю</h4>
        <div className="wk-cour">
          {wkCour.map(c => (
            <div className="wk-cour-row" key={c.courier}>
              <Bike size={14} />
              <span className="wk-cour-name">{c.courier}</span>
              <span className="wk-cour-n">{c.delivered} выдано</span>
              {c.avg_cycle_min != null && <span className="wk-cour-cyc">цикл {c.avg_cycle_min} мин</span>}
            </div>
          ))}
        </div>
        </>)}

        <h4>История заказов</h4>
        <div className="hist-bar">
          <div className="pseg" role="group" aria-label="Период истории">
            {[["1", "Сегодня"], ["2", "2 дня"], ["7", "7 дней"], ["31", "31 день"]].map(([v, label]) => (
              <button type="button" key={v} className={histDays === v ? "on" : ""}
                onClick={() => { setHistDays(v); void loadHistory(v); }}>{label}</button>
            ))}
          </div>
          <span className="hist-links">
            <a href="/report/day" target="_blank" rel="noopener" className="btn btn-primary"
              title="Отчёт дня для печати: Ctrl+P позволяет сохранить в PDF"><FileText size={14} />PDF</a>
            <a href={"/api/history/export?days=" + histDays} className="btn"
              title="Выгрузить историю в CSV (открывается в Excel)"><FileSpreadsheet size={14} />Excel</a>
          </span>
        </div>
        <div className="hist-list">
          {hist?.rows?.length ? (<>
          <div className="hrow hhead">
            <span>Время</span><span>Адрес</span><span>Курьер</span><span>Цикл</span>
          </div>
          {hist.rows.map((r, i) => (
            <div className={"hrow " + (r.outcome === "delivered" ? "ok" : "no")} key={i}>
              <span className="h-time"><b>{(r.closed_at || "").slice(11, 16)}</b><small>{(r.closed_at || "").slice(8, 10)}.{(r.closed_at || "").slice(5, 7)}</small></span>
              <span className="h-addr" title={r.address}>{r.address || ""}</span>
              <span className="h-cour">{r.courier || "–"}</span>
              <span className="h-res"><span className="h-badge">{r.outcome === "delivered" ? "✓" : "✕"}</span>{r.cycle_min != null ? <span className="h-cyc">{r.cycle_min + " мин"}</span> : null}{r.payment ? <span className="h-pay" title={r.pay_amount != null ? `Оплата: ${r.payment === "cash" ? "наличные" : "карта"}, ${r.pay_amount}` : "Способ оплаты записан, сумма неизвестна"}>{r.payment === "cash" ? "💵" : "💳"}{r.pay_amount != null ? ` ${r.pay_amount}` : ""}</span> : null}</span>
            </div>
          ))}
          </>)
            : <div className="empty-list">Пока пусто</div>}
        </div>
        </>)}

        {tab === "prof" && (<>
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
        </>)}

        {tab === "team" && !!st.me?.is_admin && (<>
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
        </>)}
      </div>
    </div>
  );
}
