"use client";

import { MapPin } from "lucide-react";
import { fmtCoords, type AppState } from "@/lib/api";
import GeoInput, { type GeoItem } from "../GeoInput";

/** Подпись адреса депо: подпись из GeoInput или ручной текст. */
function orderLabelDepot(p: GeoItem) { return p.label; }

type Mutate = (method: string, path: string, body?: Record<string, unknown>) => Promise<void>;

export function PointEditForm({ st, pointEdit, pointName, setPointName, pointNote, setPointNote, pendingPoint, pickTarget, setPickTarget, mutate, showToast, closeForm }: {
  st: AppState;
  pointEdit: string;
  pointName: string;
  setPointName: (v: string) => void;
  pointNote: string;
  setPointNote: (v: string) => void;
  pendingPoint: React.RefObject<GeoItem | null>;
  pickTarget: "point" | "order" | null;
  setPickTarget: (v: "point" | "order" | null) => void;
  mutate: Mutate;
  showToast: (msg: string, err?: boolean) => void;
  closeForm: () => void;
}) {
  return (
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
  );
}
