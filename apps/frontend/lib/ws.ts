"use client";

/* Живые обновления консоли: socket.io-клиент (замена long-poll /api/rev).
 *
 * Соединение идёт НАПРЯМУЮ с API-хостом (NEXT_PUBLIC_WS_URL; деволт для
 * dev — локальный бэк), мимо прокси фронта: у Vercel upgrade WebSocket
 * не форвардится (426), а long-poll он буферизует до 25 с.
 *
 * Auth на handshake: токен = подпись cookie-сессии (HttpOnly cookie из JS
 * не читается — берём через /api/ws-token тем же same-origin fetch'ем).
 * auth-функция вызывается на КАЖДУЮ попытку соединения: reconnect после
 * F5/смены точки берёт свежие token+point без пересоздания сокета. */

import { io, type Socket } from "socket.io-client";
import { fetchApi } from "@/lib/api";

const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL ||
  (typeof window !== "undefined" && !/^(localhost|127\.)/.test(window.location.hostname)
    ? window.location.origin // прод без env: предполагаем API за тем же хостом
    : "http://127.0.0.1:5050"); // локальная разработка

let socket: Socket | null = null;
let token: string | null = null;

/* Статус соединения для индикатора в шапке */
export type WsConnState = "connecting" | "online" | "offline";

let connState: WsConnState = "connecting";
const connListeners = new Set<(s: WsConnState) => void>();

export function subscribeConn(fn: (s: WsConnState) => void): () => void {
  connListeners.add(fn);
  fn(connState);
  return () => { connListeners.delete(fn); };
}

function setConn(s: WsConnState): void {
  connState = s;
  connListeners.forEach((fn) => fn(s));
}

export function setWsToken(t: string | null): void {
  token = t ?? null;
}

export async function ensureWsToken(): Promise<string | null> {
  if (token) return token;
  try {
    const r = await fetchApi("/api/ws-token", { headers: { Accept: "application/json" } });
    if (r.status === 401) { window.location.assign("/login"); return null; }
    const d = await r.json();
    if (typeof d?.token === "string") { token = d.token; return token; }
  } catch { /* сеть — попробуем ещё раз при следующем эффекте */ }
  return null;
}

function authPayload(): { token: string | null; point: string } {
  let point = "";
  try { point = localStorage.getItem("workPoint") || ""; } catch { /* private mode */ }
  return { token, point };
}

export function getSocket(): Socket {
  if (socket) return socket;
  socket = io(WS_URL, {
    auth: (cb) => cb(authPayload()),
    transports: ["websocket", "polling"],
    reconnection: true,
  });
  socket.on("connect", () => setConn("online"));
  socket.on("disconnect", () => setConn("offline"));
  socket.on("connect_error", (err: Error & { message?: string }) => {
    setConn("offline");
    // протухший токен (сессия умерла, а cookie ещё жив?) — перевыпустим
    if (/unauthorized|token|auth/i.test(err.message || "")) token = null;
  });
  return socket;
}

export function joinDepot(pointId: string): void {
  socket?.emit("workpoint", { point_id: pointId });
}

export function dropSocket(): void {
  socket?.removeAllListeners();
  socket?.disconnect();
  socket = null;
  token = null;
  setConn("connecting");
}
