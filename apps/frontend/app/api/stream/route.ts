import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

// Прозрачный SSE-прокси: next.config rewrites буферизуют event-stream,
// а роут-хендлер отдаёт тело потока сразу, как только бэкенд его прислал.
export async function GET(req: NextRequest) {
  const back = process.env.BACKEND_URL || "http://127.0.0.1:5050";
  const cookie = req.headers.get("cookie") || "";
  try {
    const upstream = await fetch(`${back}/api/stream`, {
      headers: { cookie },
      cache: "no-store",
    });
    if (!upstream.ok || !upstream.body) {
      return new Response("upstream error", { status: upstream.status || 502 });
    }
    return new Response(upstream.body, {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        Connection: "keep-alive",
      },
    });
  } catch {
    return new Response("backend unreachable", { status: 502 });
  }
}
