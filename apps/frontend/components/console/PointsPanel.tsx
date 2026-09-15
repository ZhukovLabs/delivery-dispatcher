"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, MapPin, Pencil, ShieldCheck, Trash2, Users } from "lucide-react";
import { fmtCoords, type AppState } from "@/lib/api";
import GeoInput, { type GeoItem } from "../GeoInput";

/* ---------- аккордеон «Места выдачи»: список точек + форма добавления/правки ---------- */

export function AccHead({ icon, label, count, open, onClick }: {
  icon: React.ReactNode; label: string; count: number; open: boolean; onClick: () => void;
}) {
  return (
    <button className="acc-head" onClick={onClick} aria-expanded={open}>
      {icon}{label}
      <span className={"p-count" + (count ? " on" : "")}>{count}</span>
      <ChevronDown size={15} className="chev" />
    </button>
  );
}

/** Подпись адреса депо: подпись из GeoInput или ручной текст. */
function orderLabelDepot(p: GeoItem) { return p.label; }

type Mutate = (method: string, path: string, body?: Record<string, unknown>) => Promise<void>;
type Confirm = (text: string, opts?: { ok?: string; danger?: boolean }) => Promise<boolean>;

export default function PointsPanel({ st, open, onToggle, mutate, showToast, askConfirm, focusMap, pickTarget, setPickTarget, onEditChange }: {
  st: AppState;
  open: boolean;
  onToggle: () => void;
  mutate: Mutate;
  showToast: (msg: string, err?: boolean) => void;
  askConfirm: Confirm;
  focusMap: (kind: "order" | "courier" | "point", id: string) => void;
  pickTarget: "point" | "order" | null;
  setPickTarget: (v: "point" | "order" | null) => void;
  onEditChange: (pid: string | null) => void;
}) {
  // редактируемая точка: null - список закрыт, "new" - добавление, id - правка
  const [pointEdit, setPointEdit] = useState<string | null>(null);
  const pendingPoint = useRef<GeoItem | null>(null);
  const [pointNote, setPointNote] = useState("");
  const [pointName, setPointName] = useState("");

  // клик по карте при открытой правке должен обновлять редактируемую точку
  useEffect(() => { onEditChange(pointEdit); }, [pointEdit]); // eslint-disable-line react-hooks/exhaustive-deps

  const closeForm = () => {
    setPointEdit(null); pendingPoint.current = null; setPointNote(""); setPointName("");
  };

  return (
    <div className={"acc-item" + (open ? " open" : "")} data-acc="points">
      <AccHead icon={<MapPin size={15} className="acc-ico" />} label="Места выдачи"
        count={(st.points || []).length} open={open} onClick={onToggle} />
      <div className="acc-body"><div className="acc-inner">
        {pointEdit !== null ? (
          <div className="depot-edit">
            <div className="pp-form-title">{pointEdit === "new" ? "Новое место выдачи" : "Изменить место выдачи"}</div>
            <input
              className="pp-name-input" type="text" name="point_name" placeholder="Название (например, ресторан)"
              aria-label="Название места выдачи" value={pointName}
              onChange={e => setPointName(e.target.value)} />
            <div className="addrow">
              <GeoInput
                key={pointEdit}
                initial={pointEdit === "new" ? "" : (st.points?.find(p => p.id === pointEdit)?.address || "")}
                placeholder="Адрес точки (подсказки появятся)" ariaLabel="Адрес места выдачи"
                onPicked={it => {
                  pendingPoint.current = it;
                  setPointNote(it ? `точка выбрана (${fmtCoords(it)})` : "");
                }}
              />
              <button className={"iconbtn pick-btn" + (pickTarget === "point" ? " active" : "")}
                title="Отметить точку кликом по карте" aria-label="Отметить точку по карте"
                aria-pressed={pickTarget === "point"}
                onClick={() => setPickTarget(pickTarget === "point" ? null : "point")}><MapPin size={15} /></button>
            </div>
            <div className={"addnote" + (pointNote ? " show" : "")} dangerouslySetInnerHTML={{ __html: pointNote }} />
            <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
              <button className="btn btn-primary" onClick={async () => {
                const p = pendingPoint.current;
                if (!p) { showToast("Сначала выберите точку: подсказкой или 📍 по карте", true); return; }
                const payload = { name: pointName.trim(), address: orderLabelDepot(p), lat: p.lat, lng: p.lng };
                if (pointEdit === "new") await mutate("POST", "/api/points", payload);
                else await mutate("POST", "/api/points/" + pointEdit, payload);
                closeForm();
                showToast("Место выдачи сохранено");
              }}>Сохранить</button>
              <button className="btn" onClick={closeForm}>Отмена</button>
            </div>
          </div>
        ) : (
          <div className="pts">
            {(st.points || []).map((p, i) => {
              const used = st.couriers.filter(c => (c.point_id || st.points?.[0]?.id) === p.id).length;
              const adminsOn = p.admins || [];
              return (
                <div className="pp-line" key={p.id}
                  title={`${p.name}: ${p.address} · клик — показать на карте`}
                  onClick={e => {
                    if ((e.target as HTMLElement).closest("button,select,input,a")) return;
                    focusMap("point", p.id);
                  }}>
                  <span className="pp-num">{i + 1}</span>
                  <div className="pp-info">
                    <div className="pp-top">
                      <b className="pp-name">{p.name}</b>
                      <span className="pp-stats">
                        <span className="pp-stat" title={`Курьеров на точке: ${used}`}><Users size={10} />{used}</span>
                        <span className={"pp-stat adm" + (adminsOn.length ? " on" : "")}
                          title={adminsOn.length ? `Администраторов онлайн: ${adminsOn.length} (${adminsOn.join(", ")})` : "Администраторов онлайн: нет"}>
                          <ShieldCheck size={10} />{adminsOn.length}
                        </span>
                      </span>
                    </div>
                    <span className="pp-addr">{p.address}</span>
                  </div>
                  <span className="pp-acts">
                    <button className="pp-ib" title="Изменить" aria-label="Изменить место выдачи"
                      onClick={e => {
                        e.stopPropagation();
                        setPointEdit(p.id); setPointName(p.name);
                        setPointNote(`сейчас: ${p.address}`);        // подсказка, что точка уже стоит
                        pendingPoint.current = { label: p.address, lat: p.lat, lng: p.lng };  // сохранение без нового выбора адреса оставит точку на месте
                      }}><Pencil size={13} /></button>
                    {(st.points || []).length > 1 && (
                      <button className="pp-ib danger" title={used > 0 ? `Привязан курьер — сначала перевесьте его` : "Удалить место выдачи"} aria-label="Удалить место выдачи"
                        onClick={async e => {
                          e.stopPropagation();
                          if (used > 0) { showToast(`К точке привязаны курьеры (${used}) — сначала перевесьте их`, true); return; }
                          if (!(await askConfirm(`Удалить «${p.name}»?`, { ok: "Удалить", danger: true }))) return;
                          await mutate("DELETE", "/api/points/" + p.id);
                        }}><Trash2 size={13} /></button>
                    )}
                  </span>
                </div>
              );
            })}
            <button className="pp-add" title="Добавить место выдачи"
              onClick={() => { setPointEdit("new"); setPointName(""); setPointNote(""); pendingPoint.current = null; }}>добавить место выдачи</button>
          </div>
        )}
      </div></div>
    </div>
  );
}
