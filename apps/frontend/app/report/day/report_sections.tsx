"use client";

import type { Data } from "./report_types";

export function SummaryKpis({ d }: { d: Data }) {
  return (
    <div className="grid">
      <div className="kpi"><b>{d.summary.delivered}</b><span>выдано (доставлено)</span></div>
      <div className="kpi"><b>{d.summary.cancelled}</b><span>отменено</span></div>
      <div className="kpi">
        <b>{d.summary.avg_cycle_min != null ? d.summary.avg_cycle_min + " мин" : "–"}</b>
        <span>средний цикл заказа</span>
      </div>
      {(!!d.summary.pay_cash || !!d.summary.pay_card) && (
        <div className="kpi">
          <b>{(d.summary.pay_cash_sum || 0) + (d.summary.pay_card_sum || 0)}</b>
          <span>
            оплат собрано: {d.summary.pay_cash || 0} наличными
            {d.summary.pay_cash_sum ? ` (${d.summary.pay_cash_sum})` : ""}
            {" · "}{d.summary.pay_card || 0} картой
            {d.summary.pay_card_sum ? ` (${d.summary.pay_card_sum})` : ""}
          </span>
        </div>
      )}
    </div>
  );
}

export function CouriersSection({ d, couriers }: { d: Data; couriers: string[] }) {
  return (<>
    <h2>Курьеры</h2>
    {(d.courier_stats?.length || 0) > 0 ? (
      <table>
        <thead>
          <tr><th>Имя</th><th>км</th><th>Взяли</th><th>Доставили</th><th>Отказы</th><th>Выручка</th><th>Наличные</th><th>Картой</th><th>На работе</th></tr>
        </thead>
        <tbody>
          {d.courier_stats!.map((c) => (
            <tr key={c.courier}>
              <td>{c.courier}</td>
              <td>{c.km ? c.km.toFixed(1) : "–"}</td>
              <td>{c.taken}</td>
              <td><b>{c.delivered}</b></td>
              <td>{c.cancelled}</td>
              <td>{c.revenue ? c.revenue.toFixed(2) : "–"}</td>
              <td>{c.pay_cash || "–"}</td>
              <td>{c.pay_card || "–"}</td>
              <td>{c.work_min != null ? `${Math.floor(c.work_min / 60)} ч ${String(c.work_min % 60).padStart(2, "0")} мин` : "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    ) : couriers.length ? (
      <div>
        {couriers.map((c) => (
          <span className="cour" key={c}>
            <b>{c}</b> · выдано за смену {d.rows.filter((r) => r.courier === c && r.outcome === "delivered").length}
          </span>
        ))}
      </div>
    ) : <p className="note">Сегодня доставок не было.</p>}
  </>);
}

export function OrdersTable({ d }: { d: Data }) {
  return (<>
    <h2>Заказы дня ({d.rows.length})</h2>
    <table>
      <thead>
        <tr><th>Время</th><th>Адрес</th><th>Курьер</th><th>Дедлайн</th><th>Исход</th><th>Цикл</th><th>Оплата</th></tr>
      </thead>
      <tbody>
        {d.rows.map((r, i) => (
          <tr key={i}>
            <td className="num">{(r.closed_at || "").slice(11, 16)}</td>
            <td>{r.address}</td>
            <td>{r.courier || "–"}</td>
            <td className="num">{r.deadline || "–"}</td>
            <td className={r.outcome === "delivered" ? "ok" : "no"}>
              {r.outcome === "delivered" ? "выдан" : "отменён"}
            </td>
            <td className="num">{r.cycle_min != null ? r.cycle_min + " мин" : "–"}</td>
            <td className="num">{r.payment
              ? (r.payment === "cash" ? "наличные" : "карта") +
                (r.pay_amount != null ? ` · ${r.pay_amount}` : "")
              : "–"}</td>
          </tr>
        ))}
        {!d.rows.length && <tr><td colSpan={7} className="note">Нет записей.</td></tr>}
      </tbody>
    </table>
  </>);
}

export function RoutesTable({ d }: { d: Data }) {
  return (<>
    {d.routes.length > 0 && (
      <>
        <h2>Неразобранные заказы на момент отчёта</h2>
        <table>
          <thead><tr><th>Курьер</th><th>Маршрут</th></tr></thead>
          <tbody>
            {d.routes.map((r, i) => (
              <tr key={i}>
                <td>{r.courier_name}{r.status === "away" ? " (следующим)" : ""}</td>
                <td>{r.stops.join(" → ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </>
    )}
  </>);
}
