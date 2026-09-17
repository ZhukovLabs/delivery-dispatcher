import type { Order } from "@/lib/api";

/* ---------- общие чистые утилиты консоли ---------- */

const AVAS: [string, string][] = [
  ["#dbe7fb", "#2c5a9e"], ["#e9e2fb", "#5f47a5"], ["#fbe3f0", "#a33a75"], ["#dcf3f0", "#13756c"],
  ["#fbeed3", "#8f5d0a"], ["#e2f4ea", "#0d7041"], ["#fbe3e0", "#a04233"], ["#e3e7fb", "#474ca3"],
  ["#eef6d8", "#5a741e"], ["#ddf1fa", "#1a6784"],
];

/** Данные /api/stats/week — недельная статистика для вкладки «Статистика». */
export type WeekStats = {
  days: { day: string; delivered: number; cancelled?: number; avg_cycle_min: number | null }[];
  couriers: { courier: string; delivered: number; avg_cycle_min: number | null }[];
  on_time?: number; on_time_total?: number;
};
/** Строка истории заказов из /api/history. */
export type HistRow = { closed_at?: string; address?: string; courier?: string; outcome?: string; cycle_min?: number | null; payment?: string; pay_amount?: number | null };
export type HistData = { rows?: HistRow[]; summary?: Record<string, number | null> } | null;

export interface UndoEntry { label: string; type: string; data: Record<string, unknown>; }
export interface ToastState { msg: string; err?: boolean; act?: { label: string; fn: () => void }; }
export interface AskState { text: string; ok: string; danger: boolean; resolve: (v: boolean) => void; }

/** Инициалы для аватара: «Настя» -> «Н», «Анна Петрова» -> «АП», fallback — первая буква email. */
export function initialsOf(name: string, email: string) {
  const n = (name || "").trim();
  if (!n) return (email[0] || "?").toUpperCase();
  const w = n.split(/\s+/);
  return (w[0][0] + (w[1] ? w[1][0] : "")).toUpperCase();
}

/** Стабильный пастельный цвет аватара по email. */
export function avaOf(email: string): [string, string] {
  let h = 0;
  for (let i = 0; i < email.length; i++) h = (h * 31 + email.charCodeAt(i)) >>> 0;
  return AVAS[h % AVAS.length];
}

export const SEG_TITLES: Record<string, string> = {
  base: "На базе: отдать сейчас",
  away: "В пути",
  off: "Не участвует в расчёте",
};

/** Русское склонение: plural(3, ["заказ", "заказа", "заказов"]) -> "заказа". */
export function plural(n: number, forms: [string, string, string]) {
  const a = Math.abs(n) % 100, d = a % 10;
  if (a > 10 && a < 20) return forms[2];
  if (d > 1 && d < 5) return forms[1];
  if (d === 1) return forms[0];
  return forms[2];
}

/** Имя в дательный падеж («Ждать Настю?» -> «Ждать Насти?»). */
export function declName(n: string) {
  if (!n) return "";
  if (!/[а-яё]$/i.test(n)) return n;
  if (n.endsWith("ий")) return n.slice(0, -2) + "ия";
  if (n.endsWith("я")) return n.slice(0, -1) + "и";  if (n.endsWith("а")) return n.slice(0, -1) + "ы";
  return n + "а";
}

export const shortAddr = (s: string) =>
  s.replace(/,?\s*Гомель$/i, "").replace(/\s*сельский Совет$/i, "").replace(/(^|\s)улица\s/i, "$1").trim();

// ключ схожести адресов для поиска дублей: регистр/пробелы/город не важны
export const addrKey = (a: string) => a.trim().toLowerCase().replace(/\s+/g, " ").replace(/,?\s*гомель$/i, "");

export const orderAgeMin = (o: Order) => {
  try { return Math.max(0, Math.round((Date.now() - new Date(o.created_at).getTime()) / 60000)); }
  catch { return 0; }
};

export const dlRound = (d: Date) => {
  const x = new Date(d);
  x.setMinutes(Math.ceil(x.getMinutes() / 5) * 5, 0, 0);
  return `${String(x.getHours()).padStart(2, "0")}:${String(x.getMinutes()).padStart(2, "0")}`;
};
