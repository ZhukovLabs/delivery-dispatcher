"use client";

import type { AskState, ToastState } from "./format";
import { useBackdropClose } from "./useBackdropClose";

/* ---------- мелкие оверлеи: баннер выбора точки, справка, подтверждение, тост ---------- */

export function PickBanner({ kind, onCancel }: { kind: "point" | "order"; onCancel: () => void }) {
  return (
    <div className="pick-banner">
      📍 Кликните по карте: <b>{kind === "point" ? "точка выдачи сохранится сразу" : "каждый клик добавляет заказ"}</b>
      <button onClick={onCancel}>Отмена</button>
    </div>
  );
}

export function HelpOverlay({ onClose }: { onClose: () => void }) {
  const bg = useBackdropClose(onClose);
  return (
    <div className="help-overlay" {...bg}>
      <div className="help-card">
        <button className="close" style={{ float: "right", border: "none", background: "transparent", fontSize: 16, cursor: "pointer", color: "#6d7688" }}
          aria-label="Закрыть" onClick={onClose}>✕</button>
        <h3>Как пользоваться</h3>
        <ol>
          <li>Поставьте <b>точку ресторана</b>: строка сверху, адрес с подсказками. Или кликните по карте.</li>
          <li>Добавьте <b>курьеров</b> и отметьте, кто сейчас «На базе».</li>
          <li>Занесите <b>готовые заказы</b>: адресом с подсказками или кликом по карте.</li>
          <li>Нажмите <b>«Рассчитать развозку»</b>. Карточка «отдать сейчас» и есть задание курьеру на базе.</li>
        </ol>
        <p className="note">План пересчитывается сам после изменений. Настройки, история и профиль — шестерёнка в шапке.</p>
      </div>
    </div>
  );
}

export function AskDialog({ ask, onResolve }: { ask: AskState; onResolve: (v: boolean) => void }) {
  const bg = useBackdropClose(() => onResolve(false));
  return (
    <div className="help-overlay" role="dialog" aria-modal="true" {...bg}>
      <div className="help-card ask-card">
        <h3>{ask.text}</h3>
        <div className="ask-btns">
          <button className="btn" autoFocus onClick={() => onResolve(false)}>Отмена</button>
          <button className={"btn " + (ask.danger ? "danger" : "btn-primary")}
            onClick={() => onResolve(true)}>{ask.ok}</button>
        </div>
      </div>
    </div>
  );
}

export function SolveOverlay({ offline }: { offline: boolean }) {
  // полный экран: расчёт развозки идёт (у нас или у другого диспетчера депо);
  // если связь потерялась посреди расчёта — говорим об этом и не держим
  // блокировку дольше минуты (после восстановления всё сверится с сервером)
  return (
    <div className="solve-ov" role="status" aria-live="assertive"
      aria-label={offline ? "Связь потеряна во время расчёта" : "Идёт расчёт развозки"}>
      <div className="solve-ov-box">
        <span className={"solve-ov-spin" + (offline ? " warn" : "")} aria-hidden="true" />
        <h3>{offline ? "Связь потеряна" : "Идёт расчёт развозки"}</h3>
        <p>{offline
          ? "Расчёт шёл, когда пропал интернет. Восстанавливаем связь — страница разблокируется сама (не дольше минуты)"
          : "Данные обновятся автоматически — страница разблокируется сама"}</p>
      </div>
    </div>
  );
}

export function Toast({ toast, onClose }: { toast: ToastState; onClose: () => void }) {
  return (
    <div id="toast" className={(toast.err ? "err " : "") + "show"}>
      <span>{toast.msg}</span>
      {toast.act && (
        <button id="toastAct" onClick={() => { onClose(); toast.act!.fn(); }}>{toast.act.label}</button>
      )}
    </div>
  );
}
