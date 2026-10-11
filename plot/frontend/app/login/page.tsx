"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [setup, setSetup] = useState<boolean | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<{ setup_required: boolean; authenticated: boolean }>("/auth/state")
      .then((s) => {
        if (s.authenticated) router.replace("/");
        setSetup(s.setup_required);
      })
      .catch((e) => setErr(`API unreachable: ${e.message}`));
  }, [router]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await api(setup ? "/auth/setup" : "/auth/login", { method: "POST", json: { email, password } });
      router.replace("/");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <form className="card login-card stack" onSubmit={submit}>
        <div>
          <div style={{ fontFamily: "var(--serif)", fontSize: 30, letterSpacing: "0.08em" }}>PLOT</div>
          <div className="muted">Private land &amp; small-home sourcing</div>
        </div>
        {setup ? <div className="notice info">First run: create the owner account (password at least 12 characters).</div> : null}
        <label className="f">
          Email
          <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label className="f">
          Password
          <input type="password" autoComplete={setup ? "new-password" : "current-password"} value={password} onChange={(e) => setPassword(e.target.value)} required minLength={setup ? 12 : 1} />
        </label>
        {err ? <div className="err small">{err}</div> : null}
        <button className="btn primary" disabled={busy || setup === null}>
          {setup ? "Create account" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
