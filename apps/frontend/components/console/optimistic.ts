import type { AppState } from "@/lib/api";

/* ---------- оптимистичные патчи состояния под конкретный API-вызов ----------
   Мгновенный отклик UI, серверная правда при ответе, откат при ошибке (mutate). */

export function optimisticFor(method: string, path: string, body: Record<string, any> | undefined): ((s: AppState) => AppState) | undefined {
  const patch = (fn: (s: AppState) => void): ((s: AppState) => AppState) =>
    (s: AppState) => { const c = { ...s, orders: [...s.orders], couriers: [...s.couriers] }; fn(c); return c; };
  if (method === "POST" && path === "/api/orders" && body?.lat !== undefined)
    return patch(s => { s.orders.push({ id: "tmp-" + Date.now(), address: String(body.address || "Точка"), lat: body.lat, lng: body.lng, point_id: body.point_id ? String(body.point_id) : undefined, created_at: new Date().toISOString(), status: "ready" }); });
  if (method === "POST" && path === "/api/orders/assign" && Array.isArray(body?.order_ids))
    return patch(s => { const name = s.couriers.find(c => c.id === body.courier_id)?.name || ""; s.orders = s.orders.map(o => body.order_ids.includes(o.id) ? { ...o, status: "out" as const, assigned: name } : o); });
  if (method === "POST" && /^\/api\/orders\/[^/]+\/return$/.test(path))
    return patch(s => { s.orders = s.orders.map(o => o.id === path.split("/")[3] ? { ...o, status: "ready" as const, assigned: "" } : o); });
  if (method === "DELETE" && path.startsWith("/api/orders/"))
    return patch(s => { s.orders = s.orders.filter(o => o.id !== path.split("/")[3]); });
  if (method === "PATCH" && path.startsWith("/api/orders/")) {
    const oid = path.split("/")[3];
    if (body?.prio !== undefined) return patch(s => { s.orders = s.orders.map(o => o.id === oid ? { ...o, prio: !!body.prio } : o); });
    if (body?.deadline !== undefined) return patch(s => { s.orders = s.orders.map(o => o.id === oid ? { ...o, deadline: String(body.deadline) } : o); });
    return undefined;
  }
  if (method === "POST" && path === "/api/points" && body?.lat !== undefined)
    return patch(s => { s.points = [...(s.points || []), { id: "tmp-" + Date.now(), name: String(body.name || "Точка"), address: String(body.address || "…"), lat: body.lat, lng: body.lng }]; });
  if (method === "POST" && /^\/api\/points\/[^/]+$/.test(path) && body)
    return patch(s => { const pid = path.split("/")[3]; s.points = (s.points || []).map(p => p.id === pid ? { ...p, ...(body.name !== undefined ? { name: String(body.name) } : {}), ...(body.lat !== undefined ? { address: String(body.address || p.address), lat: body.lat, lng: body.lng } : {}) } : p); });
  if (method === "DELETE" && /^\/api\/points\/[^/]+$/.test(path))
    return patch(s => { const pid = path.split("/")[3]; s.points = (s.points || []).filter(p => p.id !== pid); });
  if (method === "POST" && /^\/api\/couriers\/[^/]+\/point$/.test(path))
    return patch(s => { const cid = path.split("/")[3]; s.couriers = s.couriers.map(c => c.id === cid ? { ...c, point_id: String(body?.point_id || "") } : c); });
  if (method === "POST" && path === "/api/couriers" && body?.name)
    return patch(s => { s.couriers.push({ id: "tmp-" + Date.now(), name: String(body.name), status: "base" }); });
  if (method === "DELETE" && path.startsWith("/api/couriers/"))
    return patch(s => { const cid = path.split("/")[3]; const name = s.couriers.find(c => c.id === cid)?.name; s.couriers = s.couriers.filter(c => c.id !== cid); if (name) s.orders = s.orders.map(o => o.assigned === name ? { ...o, status: "ready" as const, assigned: "" } : o); });
  if (method === "POST" && /^\/api\/couriers\/[^/]+\/returned$/.test(path))
    return patch(s => { const cid = path.split("/")[3]; const name = s.couriers.find(c => c.id === cid)?.name; s.couriers = s.couriers.map(c => c.id === cid ? { ...c, status: "base" as const, back_min: 0 } : c); if (name) s.orders = s.orders.filter(o => o.assigned !== name); });
  if (method === "PATCH" && path.startsWith("/api/couriers/")) {
    const cid = path.split("/")[3];
    if (body?.status !== undefined) return patch(s => { s.couriers = s.couriers.map(c => c.id === cid ? { ...c, status: body.status } : c); });
    if (body?.back_min !== undefined) return patch(s => { s.couriers = s.couriers.map(c => c.id === cid ? { ...c, back_min: +body.back_min || 0 } : c); });
    return undefined;
  }
  if (method === "POST" && path === "/api/settings" && body)
    return patch(s => { s.settings = { ...s.settings, ...body as Record<string, number> }; });
  return undefined;
}
