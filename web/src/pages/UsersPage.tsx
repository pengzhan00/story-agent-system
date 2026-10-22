import { useState, type FormEvent } from "react";
import { useAccounts, useCreateAccount, usePatchAccount, useReviewAge, type AccountRow } from "../api/auth";
import { useMeta } from "../api/hooks";
import { ErrorNote } from "../components/ErrorNote";
import { useT } from "../i18n";
import { roleLabel } from "../lib/labels";

/** Age review of one account: the date the person gave, an optional correction, approve / reject. */
function AgeCell({ user }: { user: AccountRow }) {
  const t = useT();
  const review = useReviewAge();
  const [date, setDate] = useState("");
  const chip = user.age_status === "approved" ? "completed" : user.age_status === "rejected" ? "failed" : user.age_status === "pending" ? "stale" : "queued";
  return (
    <div className="stack small">
      <div className="row">
        <span className={`chip chip-${chip}`}>{t(`web.users.age_status.${user.age_status}`)}</span>
        {user.age !== null ? <span>{t("web.users.age_years", { n: user.age })}</span> : null}
      </div>
      {user.age_claim ? <span className="muted">{`${t("web.users.age_claim")}: ${user.age_claim}`}</span> : null}
      {user.birth_date ? <span className="muted">{`${t("web.users.birth_date")}: ${user.birth_date}`}</span> : null}
      <div className="row">
        <input type="date" aria-label={t("web.users.birth_date")} value={date} onChange={(e) => setDate(e.target.value)} />
        <button className="primary" disabled={review.isPending || (!date && !user.age_claim)} onClick={() => review.mutate({ id: user.id, approve: true, birth_date: date, note: "" })}>
          {t("web.users.age_approve")}
        </button>
        <button disabled={review.isPending} onClick={() => review.mutate({ id: user.id, approve: false, birth_date: "", note: "" })}>
          {t("web.users.age_reject")}
        </button>
      </div>
      <ErrorNote error={review.error} />
    </div>
  );
}

/** Accounts of the team (administrators only): add people, change a role, switch an account off, reset a password. */
export function UsersPage() {
  const t = useT();
  const users = useAccounts();
  const meta = useMeta();
  const create = useCreateAccount();
  const patch = usePatchAccount();
  const roles = meta.data?.auth_roles ?? [];
  const [form, setForm] = useState({ username: "", display_name: "", password: "", role: "", birth_date: "" });
  const role = form.role || roles[roles.length - 1] || "";
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate({ ...form, role }, { onSuccess: () => setForm({ username: "", display_name: "", password: "", role: "", birth_date: "" }) });
  };
  const resetPassword = (id: number) => {
    const next = window.prompt(t("web.users.new_password_prompt"));
    if (next) patch.mutate({ id, password: next });
  };
  return (
    <div className="stack">
      <h1>{t("web.nav.users")}</h1>
      <ErrorNote error={users.error ?? patch.error} />
      <form className="form-grid" onSubmit={submit}>
        <label className="field">
          <span>{t("web.auth.username")}</span>
          <input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required />
        </label>
        <label className="field">
          <span>{t("web.users.display_name")}</span>
          <input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
        </label>
        <label className="field">
          <span>{t("web.auth.password")}</span>
          <input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} autoComplete="new-password" required />
        </label>
        <label className="field">
          <span>{t("web.users.role")}</span>
          <select value={role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
            {roles.map((r) => (
              <option key={r} value={r}>
                {roleLabel(t, r)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>{t("web.users.birth_optional")}</span>
          <input type="date" value={form.birth_date} onChange={(e) => setForm({ ...form, birth_date: e.target.value })} />
        </label>
        <div className="wide">
          <ErrorNote error={create.error} />
          <button className="primary" type="submit" disabled={create.isPending || !form.username || !form.password}>
            {t("web.users.add")}
          </button>
        </div>
      </form>
      {users.isLoading ? <p className="muted">{t("web.loading")}</p> : null}
      {users.data && users.data.some((u) => u.age_status === "pending") ? (
        <p className="note">{t("web.users.age_pending_n", { n: users.data.filter((u) => u.age_status === "pending").length })}</p>
      ) : null}
      {users.data ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("web.auth.username")}</th>
                <th>{t("web.users.display_name")}</th>
                <th>{t("web.users.role")}</th>
                <th>{t("web.users.age")}</th>
                <th>{t("web.users.state")}</th>
                <th>{t("web.users.last_login")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {users.data.map((u) => (
                <tr key={u.id}>
                  <td className="strong">{u.username}</td>
                  <td>{u.display_name || "-"}</td>
                  <td>
                    <select value={u.role} onChange={(e) => patch.mutate({ id: u.id, role: e.target.value })} aria-label={t("web.users.role")}>
                      {roles.map((r) => (
                        <option key={r} value={r}>
                          {roleLabel(t, r)}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <AgeCell user={u} />
                  </td>
                  <td>
                    <span className={`chip chip-${u.active ? "completed" : "stale"}`}>{t(u.active ? "web.users.active" : "web.users.inactive")}</span>
                  </td>
                  <td className="muted small">{u.last_login_at ?? "-"}</td>
                  <td className="row-actions">
                    <button onClick={() => patch.mutate({ id: u.id, active: !u.active })}>
                      {t(u.active ? "web.users.deactivate" : "web.users.activate")}
                    </button>
                    <button onClick={() => resetPassword(u.id)}>{t("web.users.reset_password")}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
