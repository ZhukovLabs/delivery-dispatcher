"use client";

import { Bike, FileSpreadsheet, FileText } from "lucide-react";
import type { useHistState } from "./useHistState";

export default function HistTab({ api: h }: { api: ReturnType<typeof useHistState> }) {
  const { histDays, setHistDays, hist, weekStats, courStats, csvBusy, exportCsv, loadHistory } = h;
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

  return (<>
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

    {(courStats?.rows?.length || 0) > 0 && (<>
    <h4>Курьеры сегодня</h4>
    <div className="cs-table" role="table" aria-label="Статистика курьеров за сегодня">
      <div className="cs-row cs-head" role="row">
        <span>Имя</span><span>км</span><span>взяли</span><span>доставили</span>
        <span>отказы</span><span>выручка</span><span>наличные</span><span>карта</span><span>на работе</span>
      </div>
      {courStats!.rows.map(c => (
        <div className="cs-row" role="row" key={c.courier}>
          <span className="cs-name">{c.courier}</span>
          <span>{c.km ? c.km.toFixed(1) : "–"}</span>
          <span>{c.taken}</span>
          <span><b>{c.delivered}</b></span>
          <span className={c.cancelled ? "cs-bad" : ""}>{c.cancelled}</span>
          <span>{c.revenue ? c.revenue.toFixed(2) : "–"}</span>
          <span>{c.pay_cash || "–"}</span>
          <span>{c.pay_card || "–"}</span>
          <span>{c.work_min != null ? `${Math.floor(c.work_min / 60)} ч ${String(c.work_min % 60).padStart(2, "0")} мин` : "–"}</span>
        </div>
      ))}
    </div>
    <div className="cs-note">км — по живой геолокации; «на работе» — от первой выдачи до последнего закрытия (или сейчас, если развозка ещё идёт)</div>
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
        <button type="button" className="btn" onClick={() => void exportCsv()} disabled={csvBusy}
          title="Выгрузить историю в CSV (открывается в Excel)">
          <FileSpreadsheet size={14} />{csvBusy ? "Готовлю…" : "Excel"}</button>
      </span>
    </div>
    <div className="hist-list">
      {hist?.rows?.length ? (<>
      <div className="hrow hhead">
        <span>Время</span><span>Адрес</span><span>Курьер</span><span>Итог</span>
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
  </>);
}
