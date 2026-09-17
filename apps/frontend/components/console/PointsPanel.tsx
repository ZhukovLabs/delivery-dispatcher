"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, MapPin, Pencil, ShieldCheck, Trash2, Users } from "lucide-react";
import { fmtCoords, type AppState } from "@/lib/api";
import { type GeoItem } from "../GeoInput";
import { PointEditForm } from "./PointEditForm";

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

type Mutate = (method: string, path: string, body?: Record<string, unknown>) => Promise<void>;
type Confirm = (text: string, opts?: { ok?: string; danger?: boolean }) => Promise<boolean>;

export default function PointsPanel({ st, open, onToggle, mutate, showToast, askConfirm, focusMap, pickTarget, setPickTarget, onEditChange, registerPointPick }: {
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
  registerPointPick: (cb: ((ll: { lat: number; lng: number }) => void) | null) => void;
}) {
  // редактируемая точка: null - список закрыт, "new" - добавление, id - правка
  const [pointEdit, setPointEdit] = useState<string | null>(null);
  const pendingPoint = useRef<GeoItem | null>(null);
  const [pointNote, setPointNote] = useState("");
  const [pointName, setPointName] = useState("");

  // клик по карте при открытой правке должен обновлять редактируемую точку
  useEffect(() => { onEditChange(pointEdit); }, [pointEdit]); // eslint-disable-line react-hooks/exhaustive-deps

  // клик по карте в режиме пика заполняет форму: адрес правки сохраняем прежним,
  // для новой точки оставляем пустым (бэк сделает reverse-geocode)
  useEffect(() => {
    registerPointPick(ll => {
      const keep = pointEdit !== "new" && pointEdit
        ? (st.points?.find(p => p.id === pointEdit)?.address || "") : "";
      pendingPoint.current = { label: keep, lat: ll.lat, lng: ll.lng };
      setPointNote(`точка выбрана (${fmtCoords(ll)})`);
    });
    return () => registerPointPick(null);
  }, [pointEdit, st.points, registerPointPick]);

  const closeForm = () => {
    setPointEdit(null); pendingPoint.current = null; setPointNote(""); setPointName("");
  };

  return (
    <div className={"acc-item" + (open ? " open" : "")} data-acc="points">
      <AccHead icon={<MapPin size={15} className="acc-ico" />} label="Места выдачи"
        count={(st.points || []).length} open={open} onClick={onToggle} />
      <div className="acc-body"><div className="acc-inner">
        {pointEdit !== null ? (
          <PointEditForm
            st={st} pointEdit={pointEdit}
            pointName={pointName} setPointName={setPointName}
            pointNote={pointNote} setPointNote={setPointNote}
            pendingPoint={pendingPoint}
            pickTarget={pickTarget} setPickTarget={setPickTarget}
            mutate={mutate} showToast={showToast} closeForm={closeForm}
          />
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
