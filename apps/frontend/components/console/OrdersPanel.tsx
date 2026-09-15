"use client";

import { useMemo, useRef, useState } from "react";
import { Bike, Check, Copy, Flame, Hourglass, Loader2, MapPin, Package, PackageOpen, Plus, Timer, Undo2, X, Zap } from "lucide-react";
import { fmtCoords, type AppState, type Order } from "@/lib/api";
import GeoInput, { type GeoItem } from "../GeoInput";
import { AccHead } from "./PointsPanel";
import DlPop from "./DlPop";
import { addrKey, dlRound, orderAgeMin } from "./format";

/* ---------- аккордеон «Заказы»: ввод адреса, очередь готовых, развозимые ---------- */

type Mutate = (method: string, path: string, body?: Record<string, unknown>) => Promise<void>;
type Confirm = (text: string, opts?: { ok?: string; danger?: boolean }) => Promise<boolean>;
type ShowToast = (msg: string, err?: boolean, act?: { label: string; fn: () => void }) => void;

export default function OrdersPanel({ st, tick, open, onToggle, mutate, showToast, undoToast, pushUndo, doUndo, focusMap, setHoverOid, cardHl, pinning, dupOids, pickTarget, setPickTarget, onSolveEnter }: {
  st: AppState;
  tick: number;
  open: boolean;
  onToggle: () => void;
  mutate: Mutate;
  showToast: ShowToast;
  undoToast: (msg: string, label: string, type: string, data: Record<string, unknown>) => void;
  pushUndo: (label: string, type: string, data: Record<string, unknown>) => void;
  doUndo: () => Promise<void>;
  focusMap: (kind: "order" | "courier" | "point", id: string) => void;
  setHoverOid: (oid: string | null) => void;
  cardHl: string | null;
  pinning: string | null;
  dupOids: Set<string>;
  pickTarget: "point" | "order" | null;
  setPickTarget: (v: "point" | "order" | null) => void;
  onSolveEnter: () => void;
}) {
  const pendingOrder = useRef<GeoItem | null>(null);
  const [orderNote, setOrderNote] = useState("");
  const orderLabel = useRef("");
  const orderInputRef = useRef<HTMLInputElement | null>(null);
  const [dlEdit, setDlEdit] = useState<string | null>(null);

  const addOrder = async () => {
    const p = pendingOrder.current;
    if (!p) { showToast("Укажите точку: подсказкой в поле или кнопкой 📍 по карте", true); return; }
    await mutate("POST", "/api/orders", { address: orderLabel.current || p.label, lat: p.lat, lng: p.lng });
    pendingOrder.current = null;
    orderLabel.current = "";
    setOrderNote("");
    orderInputRef.current?.focus();
  };

  const autoP = +(st.settings.auto_prio_min || 0);
  const nowMin = new Date().getHours() * 60 + new Date().getMinutes();
  const planLate = (o: Order) => {
    const plan = st.plan;
    if (!plan || !plan.routes) return 0;
    for (const r of plan.routes) {
      const s = (r.stops || []).find(s => s.order_id === o.id);
      if (s) return s.late_min || 0;
    }
    return 0;
  };
  // дубли адресов: два диспетчера могут добавить один адрес одновременно (#3);
  // группа встаёт на место первого вхождения адреса
  const readyOrders = useMemo(() => {
    const readyRaw = st.orders.filter(o => (o.status || "ready") === "ready");
    const emitted = new Set<string>();
    const res: Order[] = [];
    for (const o of readyRaw) {
      const k = addrKey(o.address);
      if (!dupOids.has(o.id)) { res.push(o); continue; }
      if (emitted.has(k)) continue;
      for (const x of readyRaw) {
        if (addrKey(x.address) === k) res.push(x);
      }
      emitted.add(k);
    }
    return res;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [st.orders, dupOids]);
  const outOrders = st.orders.filter(o => o.status === "out");
  const courName = (id: string) => st.couriers.find(c => c.id === id)?.name || "";

  return (
    <div className={"acc-item" + (open ? " open" : "")} data-acc="orders">
      <AccHead icon={<Package size={15} className="acc-ico" />} label="Готовые заказы"
        count={st.orders.length} open={open} onClick={onToggle} />
      <div className="acc-body"><div className="acc-inner">
        <div className="addrow">
          <GeoInput
            inputRef={orderInputRef}
            placeholder="Адрес (Enter добавит)" ariaLabel="Адрес нового заказа"
            onPicked={(it, label) => {
              orderLabel.current = label;
              if (it) {
                pendingOrder.current = it;
                setOrderNote(`точка выбрана <b>(${fmtCoords(it)})</b>. Enter или «+» добавит заказ`);
                showToast("Точка указана: " + it.label.slice(0, 70));
              } else {
                pendingOrder.current = null;
                setOrderNote("");
              }
            }}
            onEnterEmpty={onSolveEnter}
          />
          <button className={"iconbtn pick-btn" + (pickTarget === "order" ? " active" : "")}
            title="Отметить точку кликом по карте" aria-label="Отметить точку по карте"
            aria-pressed={pickTarget === "order"}
            onClick={() => setPickTarget(pickTarget === "order" ? null : "order")}><MapPin size={15} /></button>
          <button className="plus" title="Добавить заказ" aria-label="Добавить заказ" onClick={() => void addOrder()}><Plus size={15} /></button>
        </div>
        <div className={"addnote" + (orderNote ? " show" : "")} dangerouslySetInnerHTML={{ __html: orderNote }} />
        <div id="orderList" className="ents" onMouseLeave={() => setHoverOid(null)}>
          {readyOrders.length === 0 && !outOrders.length && (
            <div className="empty-state">
              <PackageOpen size={22} />
              <b>Готовых заказов нет</b>
              <span>Введите адрес выше или отметьте точку на карте</span>
            </div>
          )}
          {readyOrders.map((o, i) => {
            void tick;
            const age = orderAgeMin(o);
            const auto = autoP > 0 && age >= autoP && !o.prio;
            const late = planLate(o);
            const soon = !late && o.deadline
              ? (() => { const [h, m] = o.deadline.split(":").map(Number); return h * 60 + m - nowMin; })()
              : null;
            return (
              <div
                key={o.id}
                className={`ent ocard${o.prio ? " prio" : ""}${late > 0 ? " burning" : ""}${cardHl === o.id ? " hl" : ""}${pinning === o.id ? " adding" : ""}${dupOids.has(o.id) ? " dup" : ""}`}
                data-oid={o.id}
                draggable={pinning !== o.id}
                title={`${o.address} · перетащите на курьера, чтобы выдать сразу`}
                onDragStart={e => {
                  e.dataTransfer.setData("text/plain", "assign:" + o.id);
                  e.dataTransfer.effectAllowed = "move";
                }}
                onMouseEnter={() => setHoverOid(o.id)}
                onClick={e => {
                  if ((e.target as HTMLElement).closest("button,select,input,a")) return;
                  focusMap("order", o.id);
                }}
              >
                <span className="e-num">{pinning === o.id ? <Loader2 size={13} className="spin" /> : i + 1}</span>
                <span className="e-name">
                  {o.address}
                  {pinning === o.id && <span className="pin-chip">пересчитываем маршрут…</span>}
                  {(st.points?.length || 0) > 1 && o.point_id && st.points?.some(p => p.id === o.point_id) && (
                    <span className="opt-tag" title="Место выдачи заказа">{st.points.find(p => p.id === o.point_id)!.name}</span>
                  )}
                  {dupOids.has(o.id) && <span className="dup-chip" title="Такой адрес уже есть в списке — проверьте, не дубль ли"><Copy size={11} /> дубль</span>}
                  {o.deadline && <span className="dl-chip" title="Обещали к этому времени"><Timer size={11} /> {o.deadline}</span>}
                  {late > 0
                    ? <span className="burn-chip" title="По текущему плану к обещанному времени не успеваем"><Flame size={11} /> опоздание ~{late} мин</span>
                    : (soon !== null && 0 <= soon && soon <= 15)
                      ? <span className="soon-chip" title="Дедлайн на подходе, а заказа ещё нет в маршруте"><Hourglass size={11} /> скоро {o.deadline}</span>
                      : null}
                  {o.prio && <span className="prio-tag"><Zap size={11} /> приоритет</span>}
                  {auto && <span className="age-tag" title="Долго в очереди: в плане будет как приоритетный"><Hourglass size={11} /> {age} мин</span>}
                  {dlEdit === o.id && (
                    <DlPop
                      initial={o.deadline || dlRound(new Date(Date.now() + 30 * 60000))}
                      hasDeadline={!!o.deadline}
                      onSave={async val => {
                        await mutate("PATCH", "/api/orders/" + o.id, { deadline: val });
                        setDlEdit(null);
                      }}
                      onClose={() => setDlEdit(null)}
                    />
                  )}
                </span>
                <span className="e-acts">
                  <button title="Обещанное время доставки (дедлайн)" aria-label="Дедлайн заказа"
                    onClick={() => setDlEdit(dlEdit === o.id ? null : o.id)}><Timer size={14} /></button>
                  <button className={"prio-btn" + (o.prio ? " on" : "")}
                    title={o.prio ? "Снять приоритет" : "Приоритет: доставить как можно раньше"}
                    aria-label="Приоритет заказа"
                    onClick={() => void mutate("PATCH", "/api/orders/" + o.id, { prio: !o.prio })}><Zap size={14} /></button>
                  <button className="ok" title="Выдать курьеру (из текущего плана)" aria-label="Выдать заказ"
                    onClick={async () => {
                      const r = (st.plan?.routes || []).find(r => (r.stops || []).some(s => s.order_id === o.id));
                      if (!r) { showToast("Заказа нет в текущем плане: рассчитайте план или выдайте с маршрута", true); return; }
                      await mutate("POST", "/api/orders/assign", { order_ids: [o.id], courier_id: r.courier_id });
                      undoToast(`Выдан: ${r.courier_name}`, `выдача ${o.address || ""}`.slice(0, 60), "assign", { order_ids: [o.id] });
                    }}><Check size={14} /></button>
                  <button className="no" title="Отменить (в историю)" aria-label="Отменить заказ"
                    onClick={async () => {
                      await mutate("DELETE", "/api/orders/" + o.id, { outcome: "cancelled" });
                      pushUndo(`удаление ${o.address || ""}`.slice(0, 60), "delOrder",
                        { address: o.address, lat: o.lat, lng: o.lng, prio: o.prio, deadline: o.deadline, point_id: o.point_id });
                      showToast("Заказ отменён", false, { label: "Отменить", fn: () => void doUndo() });
                    }}><X size={14} /></button>
                </span>
              </div>
            );
          })}
          {outOrders.length > 0 && <div className="out-cap"><Bike size={13} /> В развозке</div>}
          {outOrders.map(o => (
            <div key={o.id} className="ent ocard out" data-oid={o.id} title={o.address}
              onMouseEnter={() => setHoverOid(o.id)}
              onClick={e => {
                if ((e.target as HTMLElement).closest("button,select,input,a")) return;
                focusMap("order", o.id);
              }}>
              <span className="e-num out-ico"><Bike size={14} /></span>
              <span className="e-name">{o.address} <span className="out-chip">{courName(o.assigned || "")}</span></span>
              <span className="e-acts">
                <button className="ok" title="Вернуть в очередь готовых (не доехал, передумали)" aria-label="Вернуть в очередь"
                  onClick={async () => {
                    const was = o.assigned;
                    await mutate("POST", `/api/orders/${o.id}/return`);
                    undoToast("Заказ снова в очереди", `возврат ${o.address || ""}`.slice(0, 60), "return",
                      { order_ids: [o.id], courier_id: was });
                  }}><Undo2 size={14} /></button>
                <button className="no" title="Отменить (в историю)" aria-label="Отменить заказ"
                  onClick={async () => {
                    await mutate("DELETE", "/api/orders/" + o.id, { outcome: "cancelled" });
                    pushUndo(`удаление ${o.address || ""}`.slice(0, 60), "delOrder",
                      { address: o.address, lat: o.lat, lng: o.lng, prio: o.prio, deadline: o.deadline, point_id: o.point_id });
                    showToast("Заказ отменён", false, { label: "Отменить", fn: () => void doUndo() });
                  }}><X size={14} /></button>
              </span>
            </div>
          ))}
        </div></div>
      </div>
    </div>
  );
}
