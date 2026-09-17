"use client";

import { useState } from "react";
import { CircleHelp } from "lucide-react";

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

type TipState = { f: { key: string; label: string; tip: string }; left: number; top: number; below: boolean };

export default function ParamsTab({ s, set }: {
  s: Record<string, number>;
  set: (patch: Record<string, number | boolean>) => Promise<void>;
}) {
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

  return (<>
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
  </>);
}
