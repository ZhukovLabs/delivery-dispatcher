"use client";

import { useState } from "react";
import { api, type AppState } from "@/lib/api";

export function useProfState(st: AppState, setSt: (s: AppState) => void, showToast: (msg: string, err?: boolean) => void) {
  const [pwOld, setPwOld] = useState("");
  const [pwNew, setPwNew] = useState("");
  const [profName, setProfName] = useState("");
  const [profPhone, setProfPhone] = useState("");
  const [profOpen, setProfOpen] = useState(true);
  const [pwOpen, setPwOpen] = useState(false);

  const saveProfile = async () => {
    try {
      setSt(await api<AppState>("/api/profile", "POST", { name: profName, phone: profPhone }));
      showToast("Профиль сохранён");
    } catch (e) { showToast((e as Error).message, true); }
  };
  const changePw = async () => {
    try {
      await api("/api/password", "POST", { old: pwOld, new: pwNew });
      setPwOld(""); setPwNew("");
      showToast("Пароль изменён");
    } catch (e) { showToast((e as Error).message, true); }
  };

  return {
    pwOld, setPwOld, pwNew, setPwNew,
    profName, setProfName, profPhone, setProfPhone,
    profOpen, setProfOpen, pwOpen, setPwOpen,
    saveProfile, changePw,
  };
}

export type ProfApi = ReturnType<typeof useProfState>;
