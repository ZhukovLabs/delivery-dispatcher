"use client";

import { useCallback, useRef, useState } from "react";
import { api, type AppState } from "@/lib/api";

export function useMapInteractions(deps: {
  setSt: (s: AppState) => void;
  showToast: (msg: string, err?: boolean) => void;
}) {
  const { setSt, showToast } = deps;
  /* ---------- карта: выбор точки, фокус, подсветки ---------- */
  const [pickTarget, setPickTarget] = useState<"point" | "order" | null>(null);
  const [fitSignal, setFitSignal] = useState(0);
  const [hoverOid, setHoverOid] = useState<string | null>(null);
  const [cardHl, setCardHl] = useState<string | null>(null);
  const [focus, setFocus] = useState<{ kind: "order" | "courier" | "point"; id: string; n: number } | null>(null);
  const focusMap = (kind: "order" | "courier" | "point", id: string) =>
    setFocus(f => ({ kind, id, n: (f?.n || 0) + 1 }));
  const bumpFit = () => setFitSignal(s => s + 1);

  // точка, открытая на правку в аккордеоне «Места выдачи»: клик по карте обновляет её, а не создаёт новую
  const pointEditRef = useRef<string | null>(null);
  // клик по карте при открытой форме точки идёт в форму (pendingPoint), а не в API —
  // иначе «Сохранить»/«Отмена» работают шиворот-навыворот: пик уже сохранил, кнопки его перекрывают
  const pointPickRef = useRef<((ll: { lat: number; lng: number }) => void) | null>(null);
  const registerPointPick = useCallback(
    (cb: ((ll: { lat: number; lng: number }) => void) | null) => { pointPickRef.current = cb; }, []);
  // превью несохранённой точки на карте (пик в форме места выдачи)
  const [pickPreview, setPickPreview] = useState<{ lat: number; lng: number } | null>(null);

  const onEditChange = (pid: string | null) => { pointEditRef.current = pid; if (!pid) setPickPreview(null); };

  const onMapPick = async (ll: { lat: number; lng: number }) => {
    if (!pickTarget) return;
    const target = pickTarget;
    setPickTarget(null);
    if (target === "point" && pointPickRef.current) {
      pointPickRef.current(ll);
      setPickPreview(ll);
      return;
    }
    try {
      if (target === "point") {
        showToast("Сохраняем точку…");
        const pid = pointEditRef.current;
        const s = pid && pid !== "new"
          ? await api<AppState>("/api/points/" + pid, "POST", { address: "", lat: ll.lat, lng: ll.lng })
          : await api<AppState>("/api/points", "POST", { address: "", lat: ll.lat, lng: ll.lng });
        setSt(s);
        pointEditRef.current = null;
        showToast("Точка выдачи: " + ((s.points || []).slice(-1)[0]?.address || ""));
      } else {
        showToast("Добавляем заказ…");
        const s = await api<AppState>("/api/orders", "POST", { address: "", lat: ll.lat, lng: ll.lng });
        setSt(s);
        showToast("Новый заказ: " + s.orders[s.orders.length - 1].address);
      }
    } catch (e) { showToast((e as Error).message, true); }
  };

  const onMarkerClick = (oid: string) => {
    const card = document.querySelector(`.ocard[data-oid="${oid}"]`);
    if (card) {
      card.scrollIntoView({ block: "nearest", behavior: "smooth" });
      setCardHl(oid);
      setTimeout(() => setCardHl(null), 1600);
    }
  };

  return {
    pickTarget, setPickTarget, fitSignal, bumpFit,
    hoverOid, setHoverOid, cardHl, setCardHl,
    focus, focusMap, pickPreview, setPickPreview,
    registerPointPick, onEditChange, onMapPick, onMarkerClick,
  };
}
