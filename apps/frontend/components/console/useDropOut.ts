"use client";

import { useState } from "react";

export function useDropOut(unassignStop: (oid: string) => Promise<void>) {
  const [dropOut, setDropOut] = useState(false); // тащим стоп «наружу» из маршрута
  const isStopDrag = (e: React.DragEvent) =>
    [...e.dataTransfer.types].includes("application/x-dp-stop");
  const dropOutOver = (e: React.DragEvent) => {
    if (isStopDrag(e)) { e.preventDefault(); e.dataTransfer.dropEffect = "move"; setDropOut(true); }
  };
  const dropOutDrop = async (e: React.DragEvent) => {
    const raw = e.dataTransfer.getData("application/x-dp-stop") || "";
    if (!raw) return;
    e.preventDefault(); setDropOut(false);
    await unassignStop(raw.split("|")[0]);
  };
  const dropOutLeave = (e: React.DragEvent) => {
    if (!e.currentTarget.contains(e.relatedTarget as Node)) setDropOut(false);
  };
  return { dropOut, dropOutOver, dropOutDrop, dropOutLeave };
}
