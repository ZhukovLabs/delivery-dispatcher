"use client";

import { useRef, useState } from "react";

export interface GeoItem { label: string; lat: number; lng: number; km?: number; }

interface GeoInputProps {
  placeholder: string;
  ariaLabel: string;
  onPicked: (it: GeoItem | null, label: string) => void;
  enterKeyHint?: "add" | "solve";
  onEnterEmpty?: () => void;
  inputRef?: React.RefObject<HTMLInputElement | null>;
}

/** Поле адреса с подсказками геокодера (debounce 300 мс, кэш повторных запросов, стрелки/Enter/Esc). */
const GEO_CACHE = new Map<string, GeoItem[]>();   // повторный ввод того же адреса — мгновенно, без сети
export default function GeoInput({ placeholder, ariaLabel, onPicked, onEnterEmpty, inputRef }: GeoInputProps) {
  const [val, setVal] = useState("");
  const [items, setItems] = useState<GeoItem[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const seq = useRef(0);
  const itemsRef = useRef<GeoItem[]>([]);
  const activeRef = useRef(-1);
  itemsRef.current = items;
  activeRef.current = active;

  const close = () => { setOpen(false); setActive(-1); };

  const pick = (it: GeoItem) => {
    setVal(it.label);
    onPicked(it, it.label);
    close();
  };

  const onInput = (q: string) => {
    setVal(q);
    onPicked(null, q); // пользователь правил текст — выбранная точка больше не актуальна
    if (timer.current) clearTimeout(timer.current);
    q = q.trim();
    if (q.length < 3) { close(); return; }
    const key = q.toLowerCase();
    const cached = GEO_CACHE.get(key);
    if (cached) { setItems(cached); setActive(-1); setOpen(true); return; }
    const mySeq = ++seq.current;
    timer.current = setTimeout(async () => {
      try {
        const res = await fetch("/api/geocode?q=" + encodeURIComponent(q), { headers: { Accept: "application/json" } });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "ошибка геокодера");
        const list = (Array.isArray(data) ? data : []).slice(0, 5);
        if (mySeq !== seq.current) return;          // пришёл ответ на устаревший запрос
        if (GEO_CACHE.size > 40) GEO_CACHE.clear();
        GEO_CACHE.set(key, list);
        setItems(list);
        setActive(-1);
        setOpen(true);
      } catch {
        if (mySeq !== seq.current) return;
        setItems([]);
        setOpen(true);
      }
    }, 300);
  };

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    const its = itemsRef.current;
    if (open && its.length) {
      if (e.key === "ArrowDown") { e.preventDefault(); setActive(a => Math.min(a + 1, its.length - 1)); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setActive(a => Math.max(a - 1, 0)); return; }
      if (e.key === "Enter" && activeRef.current >= 0) { e.preventDefault(); pick(its[activeRef.current]); return; }
      if (e.key === "Escape") { close(); return; }
    }
    if (e.key === "Enter" && onEnterEmpty && !val.trim()) {
      e.preventDefault();
      onEnterEmpty();
    }
  };

  return (
    <div className="autowrap">
      <input
        ref={inputRef}
        type="text"
        placeholder={placeholder}
        aria-label={ariaLabel}
        autoComplete="off"
        value={val}
        onChange={e => onInput(e.target.value)}
        onKeyDown={onKey}
        onBlur={() => setTimeout(close, 150)}
      />
      <div className={"autolist" + (open ? " open" : "")}>
        {items.length
          ? items.map((it, i) => (
            <div
              key={i}
              className={"opt" + (i === active ? " active" : "")}
              onMouseDown={e => { e.preventDefault(); pick(it); }}
            >
              {it.label}
              {it.km != null && <span className="opt-km">{it.km} км</span>}
            </div>
          ))
          : <div className="muted">ничего не найдено</div>}
      </div>
    </div>
  );
}
