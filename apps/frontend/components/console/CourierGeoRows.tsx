"use client";

import { Bike, Gauge, House, Hourglass, Link2, MapPin, Timer } from "lucide-react";
import { fmtAge, type AppState, type Courier } from "@/lib/api";

type Mutate = (method: string, path: string, body?: Record<string, unknown>) => Promise<void>;

export function CourierGeoRows({ c, st, foreign, mutate, setBindFor }: {
  c: Courier;
  st: AppState;
  foreign: boolean;
  mutate: Mutate;
  setBindFor: (c: Courier | null) => void;
}) {
  return (<>
    {c.status === "away" && (c.geo
      ? (c.geo.delivering
           ? <div className="c-row2 geo-row" title="В развозке: заказы у курьера, возврат — по живой геолокации. Только дорога до базы: старт в плане = это время + погрузка следующей партии">
               <Bike size={11} /> в развозке · вернётся ≈{c.geo.back_min} мин
             </div>
          : c.geo.has_out && c.geo.at_depot
          ? <div className="c-row2 geo-row" title="У своей точки выдачи с заказами — фиксируем загрузку (нужен простой пару минут)">
              <Hourglass size={11} /> у точки — выдача заказов…
            </div>
          : !c.geo.has_out && c.geo.at_depot
          ? <div className="c-row2 geo-row" title="На месте, ждёт когда диспетчер отдаст заказы">
              <House size={11} /> на точке — ждёт выдачи заказов
            </div>
          : c.geo.to_point_min !== undefined
          ? <div className="c-row2 geo-row" title="Заказы ещё не отданы: сначала курьер доедет до своей точки выдачи">
              <House size={11} /> едет за заказами · до точки ≈{c.geo.to_point_min} мин
            </div>
          : <div className="c-row2 geo-row" title="Возврат рассчитан по живой геолокации курьера — только дорога до базы, без погрузки. В плане старт сдвинется сильнее: это время + погрузка следующей партии">
              <Timer size={11} /> вернётся ≈{c.geo.back_min} мин (по гео)
            </div>)
   : foreign
    ? <div className="c-row2 geo-row" title="Возврат считает диспетчер его точки">
        <Timer size={11} /> вернётся ≈{c.back_min ?? 15} мин
      </div>
    : <div className="c-row2 geo-row"><Timer size={11} /> вернётся через
      <input className="backMin" type="number" name="back_min" min={0} max={480} defaultValue={c.back_min ?? 15}
        title="Через сколько минут вернётся на базу (привяжите Telegram — будет считаться сам)" aria-label="Возврат на базу, минут"
        onChange={e => void mutate("PATCH", "/api/couriers/" + c.id, { back_min: +e.target.value || 0 })} />
      мин
    </div>)}
    {c.geo?.at_order && (
      <div className="c-row2 geo-row" title="Курьер сейчас стоит у этого заказа">
        <Bike size={11} /> у заказа: {c.geo.at_order}
      </div>
    )}
    {(c.cur_kmh !== undefined || c.avg_kmh !== undefined) && (
      <div className="c-row2 spd-row">
        {c.cur_kmh !== undefined && (
          <span className={"spd-cur" + (c.cur_kmh > 0 ? " go" : "")}
            title="Скорость прямо сейчас, по живой геолокации (за последние минуты)">
            <Gauge size={11} /> {c.cur_kmh > 0 ? `${c.cur_kmh} км/ч` : "стоит"}
          </span>
        )}
        <span className="spd-avg"
          title={c.speed_src === "geo"
            ? "Средняя скорость за сегодня — замер по геолокации, участвует в расчёте маршрутов"
            : c.speed_src === "delivery"
              ? "Средняя по темпу доставок за сегодня относительно других курьеров, участвует в расчёте"
              : "Расчётная норма из настроек: замер по этому курьеру ещё не собран"}>
          ср {(c.avg_kmh ?? 0)} км/ч{c.speed_src === "geo" ? " (гео)" : c.speed_src === "delivery" ? " (темп)" : " (норма)"}
        </span>
      </div>
    )}
    {st.cfg?.tg && (
      <div className="c-row2 tg-row">
        {c.tg_chat_id
          ? <span className="tg-bound" title={`Telegram привязан: ${c.tg_login || c.tg_chat_id}`}
            onClick={() => setBindFor(c)}><Link2 size={11} /> {c.tg_login || ("ID " + c.tg_chat_id)}</span>
          : <button className="tg-btn" title="Привязать Telegram-аккаунт курьера" onClick={() => setBindFor(c)}><Link2 size={11} /> Telegram</button>}
        {c.pos && (
          <span className="tg-pos" title={`Геолокация обновлена${c.pos.live ? " (live-трансляция)" : ""}`}>
            <MapPin size={11} /> {fmtAge(c.pos.ts)} назад{c.pos.live ? " · live" : ""}
          </span>
        )}
      </div>
    )}
  </>);
}
