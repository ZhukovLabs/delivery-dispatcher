"use client";

import { useEffect, useState } from "react";
import { fetchApi } from "@/lib/api";
import type { Data } from "./report_types";
import { REPORT_CSS } from "./report_style";
import { CouriersSection, OrdersTable, RoutesTable, SummaryKpis } from "./report_sections";

export default function ReportDayPage() {
  const [d, setD] = useState<Data | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    fetchApi("/api/report/day", { headers: { Accept: "application/json" } })
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
      <style>{REPORT_CSS}</style>
      <button className="rbtn" onClick={() => window.print()}>Печать / сохранить в PDF</button>
      <h1>Отчёт о доставке за {d.today}</h1>
      <div className="sub">Сформирован в {d.now} · Диспетчер доставки · печать: Ctrl+P (сохранить как PDF)</div>

      <SummaryKpis d={d} />

      <CouriersSection d={d} couriers={couriers} />

      <OrdersTable d={d} />

      <RoutesTable d={d} />

      <p className="note">Цикл заказа: от добавления в очередь до отметки «выдан».</p>
    </main>
  );
}
