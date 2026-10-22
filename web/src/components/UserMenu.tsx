import { useState, type FormEvent } from "react";
import { NavLink } from "react-router-dom";
import { useChangePassword, useClaimAge, useLogout, useMe, type AccountRow } from "../api/auth";
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

/** The age that counts (approved by an admin), or where the request stands, with the form to give a birth date. */
function AgeSection({ user }: { user: AccountRow }) {
  const t = useT();
  const claim = useClaimAge();
  const [date, setDate] = useState("");
  return (
    <section className="stack small" aria-label={t("web.auth.age_title")}>
      <strong>{t("web.auth.age_title")}</strong>
      {user.age_status === "approved" && user.age !== null ? <span>{t("web.auth.age_approved", { n: user.age })}</span> : null}
      {user.age_status === "pending" ? <span className="muted">{t("web.auth.age_pending")}</span> : null}
      {user.age_status === "rejected" ? <span className="error-box">{t("web.auth.age_rejected", { note: user.age_note })}</span> : null}
      {user.age_status === "none" ? <span className="muted">{t("web.auth.age_none")}</span> : null}
      {user.age_status !== "approved" ? (
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            claim.mutate(date, { onSuccess: () => setDate("") });
          }}
        >
          <input type="date" aria-label={t("web.auth.birth_date")} value={date} onChange={(e) => setDate(e.target.value)} required />
          <button type="submit" disabled={claim.isPending || !date}>
            {t("web.auth.age_submit")}
          </button>
        </form>
      ) : null}
      <ErrorNote error={claim.error} />
    </section>
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
      {user.age_status === "pending" ? <span className="chip chip-stale">{t("web.users.age_status.pending")}</span> : null}
      {open ? (
        <div className="popover stack">
          <AgeSection user={user} />
          <PasswordForm onDone={() => setOpen(false)} />
          <button onClick={() => logout.mutate()}>{t("web.auth.signout")}</button>
        </div>
      ) : null}
    </div>
  );
}
