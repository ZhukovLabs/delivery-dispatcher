"use client";

import { Bike, MapPin, Package, PackageOpen, Plus } from "lucide-react";
import { fmtCoords, type AppState } from "@/lib/api";
import GeoInput from "../GeoInput";
import { AccHead } from "./PointsPanel";
import { OutOrderRows } from "./OutOrderRows";
import { ReadyOrderRows } from "./ReadyOrderRows";
import { useOrdersState } from "./useOrdersState";

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
  const {
    pendingOrder, orderNote, setOrderNote, orderLabel, orderInputRef, addOrder,
    autoP, nowMin, planLate, planExists, inPlan, readyOrders, outOrders, courName,
    dlEdit, setDlEdit,
  } = useOrdersState({ st, dupOids, mutate, showToast });

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
          <ReadyOrderRows
            st={st} tick={tick} orders={readyOrders} dupOids={dupOids}
            autoP={autoP} nowMin={nowMin} planLate={planLate} planExists={planExists} inPlan={inPlan}
            cardHl={cardHl} pinning={pinning} dlEdit={dlEdit} setDlEdit={setDlEdit}
            setHoverOid={setHoverOid} focusMap={focusMap}
            mutate={mutate} showToast={showToast} undoToast={undoToast} pushUndo={pushUndo} doUndo={doUndo}
          />
          {outOrders.length > 0 && <div className="out-cap"><Bike size={13} /> В развозке</div>}
          <OutOrderRows
            orders={outOrders} courName={courName}
            setHoverOid={setHoverOid} focusMap={focusMap}
            mutate={mutate} showToast={showToast} undoToast={undoToast} pushUndo={pushUndo} doUndo={doUndo}
          />
        </div></div>
      </div>
    </div>
  );
}
