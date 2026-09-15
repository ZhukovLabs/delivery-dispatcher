"use client";

import { useEffect, useState } from "react";

type Row = {
  closed_at: string;
  address: string;
  courier: string;
  outcome: string;
  cycle_min: number | null;
  deadline: string;
};
type Route = { courier_name: string; status: string; stops: string[] };
type Data = {
  rows: Row[];
  summary: { delivered: number; cancelled: number; avg_cycle_min: number | null };
  routes: Route[];
  today: string;
  now: string;
};

export default function ReportDayPage() {
  const [d, setD] = useState<Data | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    fetch("/api/report/day", { headers: { Accept: "application/json" } })
      .then(async (r) => {
        if (r.status === 401) throw new Error("Требуется вход — откройте диспетчерскую и войдите");
        if (!r.ok) throw new Error("Бэкенд недоступен (" + r.status + ")");
        return r.json() as Promise<Data>;
      })
      .then((j) => alive && setD(j))
      .catch((e) => alive && setErr(String(e.message || e)));
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (d) { const t = setTimeout(() => window.print(), 400); return () => clearTimeout(t); }
  }, [d]);

  if (err) return <main className="rdoc"><p className="rdoc-err">{err}</p></main>;
  if (!d) return <main className="rdoc"><p className="rdoc-err">Загрузка…</p></main>;

  const couriers = [...new Set(d.rows.map((r) => r.courier).filter(Boolean))];
  return (
    <main className="rdoc">
      <style>{`
        .rdoc { color-scheme: light; font: 14px/1.45 "Segoe UI", system-ui, sans-serif;
                color:#1c2430; margin:32px auto; max-width:780px; background:#fff; padding:0 16px; }
        .rdoc h1 { font-size:20px; margin:0 0 2px; }
        .rdoc .sub { color:#6b7684; font-size:12.5px; margin-bottom:18px; }
        .rdoc .grid { display:flex; gap:12px; margin-bottom:22px; flex-wrap:wrap; }
        .rdoc .kpi { flex:1 1 140px; border:1px solid #e3e8ef; border-radius:10px; padding:12px 14px; }
        .rdoc .kpi b { display:block; font-size:22px; font-variant-numeric:tabular-nums; }
        .rdoc .kpi span { color:#6b7684; font-size:12px; }
        .rdoc h2 { font-size:14px; margin:20px 0 8px; color:#39424e; }
        .rdoc table { width:100%; border-collapse:collapse; font-size:12.5px; }
        .rdoc th { text-align:left; color:#6b7684; font-weight:600; border-bottom:1px solid #dfe5ec; padding:5px 8px; }
        .rdoc td { border-bottom:1px solid #eef1f5; padding:5px 8px; vertical-align:top; }
        .rdoc td.num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
        .rdoc .ok { color:#0e7a3d; } .rdoc .no { color:#b3261e; }
        .rdoc .note { color:#6b7684; font-size:12px; margin-top:20px; }
        .rdoc .cour { display:inline-block; background:#f1f4f9; border-radius:12px; padding:2px 10px;
                      margin:0 6px 6px 0; font-size:12.5px; }
        .rdoc .rbtn { display:inline-block; margin:0 0 14px; padding:7px 14px; border-radius:8px;
                      border:1px solid #d5dae3; background:#fff; font:600 13px "Segoe UI",sans-serif;
                      cursor:pointer; color:#1c2430; }
        .rdoc .rdoc-err { color:#b3261e; margin-top:40px; }
        @media print { .rdoc { margin:0; } .rdoc .kpi { break-inside:avoid; } .rdoc tr { break-inside:avoid; } .rdoc .rbtn { display:none; } }
      `}</style>
      <button className="rbtn" onClick={() => window.print()}>Печать / сохранить в PDF</button>
      <h1>Отчёт о доставке за {d.today}</h1>
      <div className="sub">Сформирован в {d.now} · Диспетчер доставки · печать: Ctrl+P (сохранить как PDF)</div>

      <div className="grid">
        <div className="kpi"><b>{d.summary.delivered}</b><span>выдано (доставлено)</span></div>
        <div className="kpi"><b>{d.summary.cancelled}</b><span>отменено</span></div>
        <div className="kpi">
          <b>{d.summary.avg_cycle_min != null ? d.summary.avg_cycle_min + " мин" : "–"}</b>
          <span>средний цикл заказа</span>
        </div>
      </div>

      <h2>Курьеры</h2>
      {couriers.length ? (
        <div>
          {couriers.map((c) => (
            <span className="cour" key={c}>
              <b>{c}</b> · выдано за смену {d.rows.filter((r) => r.courier === c && r.outcome === "delivered").length}
            </span>
          ))}
        </div>
      ) : <p className="note">Сегодня доставок не было.</p>}

      <h2>Заказы дня ({d.rows.length})</h2>
      <table>
        <thead>
          <tr><th>Время</th><th>Адрес</th><th>Курьер</th><th>Дедлайн</th><th>Исход</th><th>Цикл</th></tr>
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
            </tr>
          ))}
          {!d.rows.length && <tr><td colSpan={6} className="note">Нет записей.</td></tr>}
        </tbody>
      </table>

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

      <p className="note">Цикл заказа: от добавления в очередь до отметки «выдан».</p>
    </main>
  );
}
