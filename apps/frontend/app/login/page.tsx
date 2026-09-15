"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [pwd, setPwd] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setErr("");
    try {
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ email, password: pwd }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "Неверный email или пароль");
      router.replace("/");
      router.refresh();
    } catch (e) {
      setErr((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <h1>🛵 Диспетчер доставки</h1>
        <div className="sub">войдите, чтобы управлять развозкой</div>
        <label htmlFor="loginEmail">Email</label>
        <input id="loginEmail" name="email" type="email" value={email} onChange={e => setEmail(e.target.value)} autoFocus autoComplete="username" />
        <label htmlFor="loginPwd">Пароль</label>
        <input id="loginPwd" name="password" type="password" value={pwd} onChange={e => setPwd(e.target.value)} autoComplete="current-password" />
        <button type="submit" disabled={busy}>{busy ? "Входим…" : "Войти"}</button>
        {err && <div className="err">{err}</div>}
      </form>
    </div>
  );
}
