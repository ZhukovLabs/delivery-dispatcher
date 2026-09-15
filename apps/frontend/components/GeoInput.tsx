"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export interface GeoItem { label: string; lat: number; lng: number; km?: number; }

interface GeoInputProps {
  placeholder: string;
  ariaLabel: string;
  onPicked: (it: GeoItem | null, label: string) => void;
  enterKeyHint?: "add" | "solve";
  onEnterEmpty?: () => void;
  inputRef?: React.RefObject<HTMLInputElement | null>;
  initial?: string;
}

/** Поле адреса с подсказками геокодера: debounce 300 мс, кэш TanStack Query (повтор — без сети), стрелки/Enter/Esc. */
export default function GeoInput({ placeholder, ariaLabel, onPicked, onEnterEmpty, inputRef, initial }: GeoInputProps) {
  const [val, setVal] = useState(initial || "");
  const [deb, setDeb] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const itemsRef = useRef<GeoItem[]>([]);
  const activeRef = useRef(-1);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setDeb(val.trim()), 300);
    return () => clearTimeout(t);
  }, [val]);

  // дропдаун шире поля и не обрезается скроллящимися панелями: fixed-позиция от input
  useEffect(() => {
    if (!open) return;
    const place = () => {
      const el = wrapRef.current?.querySelector("input");
      if (!el) return;
      const r = el.getBoundingClientRect();
      const width = Math.min(460, window.innerWidth - 16);
      setPos({
        top: Math.round(r.bottom + 4),
        left: Math.max(8, Math.min(Math.round(r.left), window.innerWidth - 8 - width)),
      });
    };
    place();
    const scrollOpts = { capture: true, passive: true } as AddEventListenerOptions;
    window.addEventListener("scroll", place, scrollOpts);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, scrollOpts);
      window.removeEventListener("resize", place);
    };
  }, [open, deb]);

  const { data: items = [], isPending } = useQuery({
    queryKey: ["geo", deb],
    queryFn: async ({ signal }) => {
      const list = await api<GeoItem[]>("/api/geocode?q=" + encodeURIComponent(deb), "GET", undefined, signal);
      return (Array.isArray(list) ? list : []).slice(0, 5);
    },
    enabled: deb.length >= 3,
    staleTime: 5 * 60_000,   // повторный ввод того же адреса — из кэша
    gcTime: 30 * 60_000,
    retry: 0,
  });
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
    setOpen(q.trim().length >= 3);
    onPicked(null, q); // пользователь правил текст — выбранная точка больше не актуальна
    if (q.trim().length < 3) close();
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
    <div className="autowrap" ref={wrapRef}>
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
      <div
        className={"autolist" + (open ? " open" : "")}
        style={pos ? { top: pos.top, left: pos.left } : undefined}
      >
        {isPending
          ? <div className="muted">ищем…</div>
          : items.length
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
