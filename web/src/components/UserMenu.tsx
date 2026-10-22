import { useState, type FormEvent } from "react";
import { NavLink } from "react-router-dom";
import { useChangePassword, useLogout, useMe } from "../api/auth";
import { useT } from "../i18n";
import { roleLabel } from "../lib/labels";
import { ErrorNote } from "./ErrorNote";

function PasswordForm({ onDone }: { onDone: () => void }) {
  const t = useT();
  const change = useChangePassword();
  const [oldPassword, setOld] = useState("");
  const [newPassword, setNew] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    change.mutate({ old_password: oldPassword, new_password: newPassword }, { onSuccess: onDone });
  };
  return (
    <form className="stack small" onSubmit={submit}>
      <label className="field">
        <span>{t("web.auth.old_password")}</span>
        <input type="password" value={oldPassword} onChange={(e) => setOld(e.target.value)} autoComplete="current-password" required />
      </label>
      <label className="field">
        <span>{t("web.auth.new_password")}</span>
        <input type="password" value={newPassword} onChange={(e) => setNew(e.target.value)} autoComplete="new-password" required />
      </label>
      <ErrorNote error={change.error} />
      <button type="submit" disabled={change.isPending}>
        {t("web.auth.change_password")}
      </button>
    </form>
  );
}

/** The signed-in user in the top bar: who, which role, change password, sign out (nothing shows with sign-in off). */
export function UserMenu() {
  const t = useT();
  const me = useMe();
  const logout = useLogout();
  const [open, setOpen] = useState(false);
  if (me.data?.mode !== "token" || !me.data.user) return null;
  const user = me.data.user;
  return (
    <div className="user-menu">
      {me.data.is_admin ? (
        <>
          <NavLink to="/users" className="nav-link">
            {t("web.nav.users")}
          </NavLink>
          <NavLink to="/audit" className="nav-link">
            {t("web.nav.audit")}
          </NavLink>
        </>
      ) : null}
      <button className="link" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        {user.display_name || user.username} · {roleLabel(t, user.role)}
      </button>
      {open ? (
        <div className="popover stack">
          <PasswordForm onDone={() => setOpen(false)} />
          <button onClick={() => logout.mutate()}>{t("web.auth.signout")}</button>
        </div>
      ) : null}
    </div>
  );
}
