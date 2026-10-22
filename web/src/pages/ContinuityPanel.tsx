import { useState, type FormEvent } from "react";
import { useStory, useStoryEdit } from "../api/hooks";
import { ErrorNote } from "../components/ErrorNote";
import { useT } from "../i18n";

const num = (v: unknown, fallback = 1) => (Number.isFinite(Number(v)) && Number(v) >= 1 ? Number(v) : fallback);

function Looks({ id }: { id: number }) {
  const t = useT();
  const characters = useStory(id, "characters");
  const looks = useStory(id, "looks");
  const edit = useStoryEdit(id, "looks");
  const [form, setForm] = useState({ character_id: "", label: "", costume: "", from_episode: "1", from_act: "1", from_scene: "1" });
  const names = new Map((characters.data ?? []).map((c) => [Number(c.id), String(c.name)]));
  const characterId = form.character_id || String(characters.data?.[0]?.id ?? "");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    edit.create.mutate(
      { character_id: Number(characterId), label: form.label, costume: form.costume, from_episode: num(form.from_episode), from_act: num(form.from_act), from_scene: num(form.from_scene) },
      { onSuccess: () => setForm({ ...form, label: "", costume: "" }) },
    );
  };
  return (
    <section className="stack">
      <h2>{t("web.continuity.looks")}</h2>
      <p className="muted small">{t("web.continuity.looks_hint")}</p>
      <ErrorNote error={characters.error ?? looks.error ?? edit.create.error ?? edit.update.error ?? edit.remove.error} />
      <form className="form-grid" onSubmit={submit}>
        <label className="field">
          <span>{t("web.continuity.character")}</span>
          <select value={characterId} onChange={(e) => setForm({ ...form, character_id: e.target.value })}>
            {(characters.data ?? []).map((c) => (
              <option key={String(c.id)} value={String(c.id)}>
                {String(c.name)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>{t("web.continuity.label")}</span>
          <input value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} />
        </label>
        <label className="field wide">
          <span>{t("web.continuity.costume")}</span>
          <input value={form.costume} onChange={(e) => setForm({ ...form, costume: e.target.value })} required />
        </label>
        {(["from_episode", "from_act", "from_scene"] as const).map((k) => (
          <label key={k} className="field">
            <span>{t(`web.continuity.${k}`)}</span>
            <input type="number" min={1} value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
          </label>
        ))}
        <div className="wide">
          <button className="primary" type="submit" disabled={edit.create.isPending || !form.costume.trim() || !characterId}>
            {t("web.continuity.add_look")}
          </button>
        </div>
      </form>
      {looks.data?.length === 0 ? <p className="empty">{t("web.continuity.no_looks")}</p> : null}
      {looks.data && looks.data.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("web.continuity.character")}</th>
                <th>{t("web.continuity.label")}</th>
                <th>{t("web.continuity.costume")}</th>
                <th>{t("web.continuity.from")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {looks.data.map((lk) => (
                <tr key={String(lk.id)}>
                  <td className="strong">{names.get(Number(lk.character_id)) ?? "-"}</td>
                  <td>{String(lk.label) || "-"}</td>
                  <td>
                    <input
                      defaultValue={String(lk.costume)}
                      aria-label={t("web.continuity.costume")}
                      onBlur={(e) => e.target.value.trim() && e.target.value !== lk.costume && edit.update.mutate({ itemId: Number(lk.id), body: { costume: e.target.value } })}
                    />
                  </td>
                  <td className="small muted">{`${t("web.continuity.from_episode")} ${String(lk.from_episode)} · ${t("web.continuity.from_act")} ${String(lk.from_act)} · ${t("web.continuity.from_scene")} ${String(lk.from_scene)}`}</td>
                  <td className="row-actions">
                    <button onClick={() => window.confirm(t("web.continuity.delete_confirm")) && edit.remove.mutate(Number(lk.id))}>{t("web.continuity.delete")}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

function Props({ id }: { id: number }) {
  const t = useT();
  const props = useStory(id, "props");
  const edit = useStoryEdit(id, "props");
  const [form, setForm] = useState({ name: "", description: "", introduced_episode: "1" });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    edit.create.mutate({ name: form.name, description: form.description, introduced_episode: num(form.introduced_episode) }, { onSuccess: () => setForm({ ...form, name: "", description: "" }) });
  };
  return (
    <section className="stack">
      <h2>{t("web.continuity.props")}</h2>
      <p className="muted small">{t("web.continuity.props_hint")}</p>
      <ErrorNote error={props.error ?? edit.create.error ?? edit.update.error ?? edit.remove.error} />
      <form className="form-grid" onSubmit={submit}>
        <label className="field">
          <span>{t("web.continuity.prop_name")}</span>
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
        </label>
        <label className="field">
          <span>{t("web.continuity.from_episode")}</span>
          <input type="number" min={1} value={form.introduced_episode} onChange={(e) => setForm({ ...form, introduced_episode: e.target.value })} />
        </label>
        <label className="field wide">
          <span>{t("web.continuity.prop_description")}</span>
          <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
        </label>
        <div className="wide">
          <button className="primary" type="submit" disabled={edit.create.isPending || !form.name.trim()}>
            {t("web.continuity.add_prop")}
          </button>
        </div>
      </form>
      {props.data?.length === 0 ? <p className="empty">{t("web.continuity.no_props")}</p> : null}
      {props.data && props.data.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("web.continuity.prop_name")}</th>
                <th>{t("web.continuity.prop_description")}</th>
                <th>{t("web.continuity.from_episode")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {props.data.map((p) => (
                <tr key={String(p.id)}>
                  <td className="strong">{String(p.name)}</td>
                  <td>
                    <input
                      defaultValue={String(p.description)}
                      aria-label={t("web.continuity.prop_description")}
                      onBlur={(e) => e.target.value !== p.description && edit.update.mutate({ itemId: Number(p.id), body: { description: e.target.value } })}
                    />
                  </td>
                  <td>{String(p.introduced_episode)}</td>
                  <td className="row-actions">
                    <button onClick={() => window.confirm(t("web.continuity.delete_confirm")) && edit.remove.mutate(Number(p.id))}>{t("web.continuity.delete")}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

/** What each character wears from which point of the story, and the props with one fixed description each. */
export function ContinuityPanel({ id }: { id: number }) {
  const t = useT();
  return (
    <div className="stack">
      <p className="note">{t("web.continuity.apply_hint")}</p>
      <Looks id={id} />
      <Props id={id} />
    </div>
  );
}
