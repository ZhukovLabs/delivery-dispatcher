"use client";

import { useState } from "react";
import { api, type AppState } from "@/lib/api";

export function useTeamState(
  st: AppState,
  setSt: (s: AppState) => void,
  showToast: (msg: string, err?: boolean) => void,
  askConfirm: (text: string, opts?: { ok?: string; danger?: boolean }) => Promise<boolean>,
) {
  const [userEmail, setUserEmail] = useState("");
  const [userName, setUserName] = useState("");
  const [userPhone, setUserPhone] = useState("");
  const [userPwd, setUserPwd] = useState("");
  const [userIsAdmin, setUserIsAdmin] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [eMail, setEMail] = useState(""); const [eName, setEName] = useState("");
  const [ePhone, setEPhone] = useState(""); const [eAdmin, setEAdmin] = useState(false);
  const [ePwd, setEPwd] = useState("");

  const addUser = async () => {
    try {
      const nst = await api<AppState>("/api/users", "POST", { email: userEmail, password: userPwd, is_admin: userIsAdmin, name: userName, phone: userPhone });
      setSt(nst);
      setUserEmail(""); setUserPwd(""); setUserIsAdmin(false); setUserName(""); setUserPhone("");
      showToast("Пользователь добавлен");
    } catch (e) { showToast((e as Error).message, true); }
  };
  const delUser = async (uid: string, email: string) => {
    if (!(await askConfirm(`Удалить пользователя «${email}»?`, { ok: "Удалить", danger: true }))) return;
    try { setSt(await api<AppState>("/api/users/" + uid, "DELETE")); }
    catch (e) { showToast((e as Error).message, true); }
  };
  const updUser = async (uid: string, data: { email: string; name: string; phone: string; is_admin: boolean }): Promise<boolean> => {
    try {
      setSt(await api<AppState>("/api/users/" + uid, "PUT", data));
      showToast("Изменения сохранены");
      return true;
    } catch (e) { showToast((e as Error).message, true); return false; }
  };
  const resetPwd = async (uid: string, newPwd: string): Promise<boolean> => {
    try {
      await api("/api/users/" + uid + "/password", "PUT", { new: newPwd });
      showToast("Пароль обновлён");
      return true;
    } catch (e) { showToast((e as Error).message, true); return false; }
  };

  return {
    userEmail, setUserEmail, userName, setUserName, userPhone, setUserPhone,
    userPwd, setUserPwd, userIsAdmin, setUserIsAdmin,
    addOpen, setAddOpen, editId, setEditId,
    eMail, setEMail, eName, setEName, ePhone, setEPhone, eAdmin, setEAdmin, ePwd, setEPwd,
    addUser, delUser, updUser, resetPwd,
  };
}

export type TeamApi = ReturnType<typeof useTeamState>;
