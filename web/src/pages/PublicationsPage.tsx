import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { post, put } from "../api/client";
import { controlGet, type Target, type Category, type Publication } from "../api/control";
import { useAllAssets } from "../api/library";
import { ErrorNote } from "../components/ErrorNote";
import { useT } from "../i18n";

const STATES = ["draft", "ready", "scheduled", "published", "withdrawn", "deleted", "cancelled"];
const NEXT: Record<string, string[]> = { draft: ["ready", "published", "cancelled"], ready: ["draft", "scheduled", "published", "cancelled"], scheduled: ["ready", "published", "cancelled"], published: ["withdrawn", "deleted"], withdrawn: ["published", "deleted"], deleted: [], cancelled: ["draft"] };

export function PublicationsPage() {
  const t = useT();
  const qc = useQueryClient();
  const [search] = useSearchParams();
  const assets = useAllAssets();
  const [target, setTarget] = useState("");
  const [platformFilter, setPlatformFilter] = useState("");
  const [classification, setClassification] = useState({ content_type: "", purpose: "", series: "", episode: "" });
  const [category, setCategory] = useState("");
  const [state, setState] = useState("");
  const [offset, setOffset] = useState(0);
  const targets = useQuery({ queryKey: ["pub-targets"], queryFn: () => controlGet<Target[]>("/api/publications/targets") });
  const categories = useQuery({ queryKey: ["pub-categories"], queryFn: () => controlGet<Category[]>("/api/publications/categories") });
  const query = new URLSearchParams({ platform: platformFilter, target_id: target, category_id: category, status: state, offset: String(offset), limit: "30", asset_id: search.get("asset") ?? "", ...(search.get("project") ? { project_id: search.get("project")! } : {}) });
  const list = useQuery({ queryKey: ["publications", query.toString()], queryFn: () => controlGet<{ total: number; items: Publication[] }>(`/api/publications?${query}`) });
  const [platform, setPlatform] = useState("");
  const [account, setAccount] = useState("");
  const [channel, setChannel] = useState("");
  const [categoryName, setCategoryName] = useState("");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [tags, setTags] = useState("");
  const [selected, setSelected] = useState<string[]>(search.get("asset") ? [search.get("asset")!] : []);
  const [edit, setEdit] = useState<Publication | null>(null);
  const [draftTarget, setDraftTarget] = useState("");
  const [draftCategory, setDraftCategory] = useState("");
  const [transition, setTransition] = useState<{ record: Publication; status: string } | null>(null);
  const [url, setUrl] = useState("");
  const [external, setExternal] = useState("");
  const [actual, setActual] = useState("");
  const [evidence, setEvidence] = useState("");
  const refresh = () => { void qc.invalidateQueries({ queryKey: ["publications"] }); };
  const addTarget = useMutation({ mutationFn: () => post<Target>("/api/publications/targets", { platform, account, channel }), onSuccess: x => { setDraftTarget(x.id); void qc.invalidateQueries({ queryKey: ["pub-targets"] }); } });
  const addCategory = useMutation({ mutationFn: () => post<Category>("/api/publications/categories", { name: categoryName }), onSuccess: x => { setDraftCategory(x.id); void qc.invalidateQueries({ queryKey: ["pub-categories"] }); } });
  const save = useMutation({ mutationFn: () => {
    const data = { target_id: draftTarget, category_id: draftCategory || null, project_id: edit?.project_id ?? (Number(search.get("project")) || null), title, body, classification, tags: tags.split(",").map(x => x.trim()).filter(Boolean), items: selected.map((asset_id, i) => ({ asset_id, role: i ? "attachment" : "main" })), revision: edit?.revision ?? null, request_key: edit ? undefined : crypto.randomUUID() };
    return edit ? put(`/api/publications/${edit.id}`, data) : post("/api/publications", data);
  }, onSuccess: () => { refresh(); setEdit(null); setTitle(""); setBody(""); setSelected([]); } });
  const change = useMutation({ mutationFn: () => {
    if (!transition) throw new Error(t("web.pub.no_selection"));
    return post(`/api/publications/${transition.record.id}/state`, { status: transition.status, revision: transition.record.revision, url, external_id: external, actual_published_at: transition.status === "published" && actual ? new Date(actual).toISOString() : "", scheduled_at: transition.status === "scheduled" && actual ? new Date(actual).toISOString() : "", evidence });
  }, onSuccess: () => { refresh(); setTransition(null); } });
  return <div className="stack">
    <h1>{t("web.pub.title")}</h1><p className="muted">{t("web.pub.note")}</p>
    <ErrorNote error={list.error ?? targets.error ?? categories.error ?? assets.error ?? addTarget.error ?? addCategory.error ?? save.error ?? change.error} />
    <details className="panel"><summary>{t("web.pub.manage")}</summary><div className="stack">
      <label>{t("web.pub.platform")}<input value={platform} onChange={e => setPlatform(e.target.value)} /></label>
      <label>{t("web.pub.account")}<input value={account} onChange={e => setAccount(e.target.value)} /></label>
      <label>{t("web.pub.channel")}<input value={channel} onChange={e => setChannel(e.target.value)} /></label>
      <button disabled={!platform || !account || addTarget.isPending} onClick={() => addTarget.mutate()}>{t("web.pub.add_target")}</button>
      <label>{t("web.pub.category")}<input value={categoryName} onChange={e => setCategoryName(e.target.value)} /></label>
      <button disabled={!categoryName || addCategory.isPending} onClick={() => addCategory.mutate()}>{t("web.pub.add_category")}</button>
    </div></details>
    <section className="panel stack"><h2>{t(edit ? "web.pub.edit" : "web.pub.create")}</h2>
      <label>{t("web.pub.target")}<select value={draftTarget} disabled={Boolean(edit)} onChange={e => setDraftTarget(e.target.value)}><option value="">{t("web.pub.pick")}</option>{targets.data?.map(x => <option key={x.id} value={x.id}>{`${x.platform} / ${x.account} / ${x.channel}`}</option>)}</select></label>
      <label>{t("web.pub.category")}<select value={draftCategory} onChange={e => setDraftCategory(e.target.value)}><option value="">{t("web.pub.none")}</option>{categories.data?.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>
      <label>{t("web.pub.post_title")}<input value={title} onChange={e => setTitle(e.target.value)} /></label>
      {(Object.keys(classification) as (keyof typeof classification)[]).map(key => <label key={key}>{t(`web.pub.classification.${key}`)}<input value={classification[key]} onChange={e => setClassification(old => ({ ...old, [key]: e.target.value }))} /></label>)}
      <label>{t("web.pub.body")}<textarea value={body} onChange={e => setBody(e.target.value)} /></label>
      <label>{t("web.pub.tags")}<input value={tags} onChange={e => setTags(e.target.value)} /></label>
      <fieldset><legend>{t("web.pub.assets")}</legend><div className="stack">
        {assets.data?.filter(a => !a.trashed_at && !a.locked).map(a => <label key={a.id}><input type="checkbox" checked={selected.includes(a.id)} onChange={e => setSelected(old => e.target.checked ? [...old, a.id] : old.filter(id => id !== a.id))} />{`${a.kind} · ${a.id.slice(0, 8)} · ${a.width ?? ""}${a.height ? `×${a.height}` : ""}`}</label>)}
      </div></fieldset>
      <button className="primary" disabled={!title || !draftTarget || !selected.length || save.isPending} onClick={() => save.mutate()}>{t("web.pub.save")}</button>
      {edit && <button onClick={() => { setEdit(null); setTitle(""); setBody(""); setSelected([]); }}>{t("web.pub.cancel")}</button>}
    </section>
    <div className="row">
      <select aria-label={t("web.pub.filter_platform")} value={platformFilter} onChange={e => { setPlatformFilter(e.target.value); setOffset(0); }}><option value="">{t("web.pub.all_platforms")}</option>{[...new Set(targets.data?.map(x => x.platform) ?? [])].map(x => <option key={x} value={x}>{x}</option>)}</select>
      <select aria-label={t("web.pub.target")} value={target} onChange={e => { setTarget(e.target.value); setOffset(0); }}><option value="">{t("web.pub.all_targets")}</option>{targets.data?.map(x => <option key={x.id} value={x.id}>{`${x.platform} / ${x.account} / ${x.channel}`}</option>)}</select>
      <select aria-label={t("web.pub.category")} value={category} onChange={e => { setCategory(e.target.value); setOffset(0); }}><option value="">{t("web.pub.all_categories")}</option>{categories.data?.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</select>
      <select aria-label={t("web.pub.state")} value={state} onChange={e => { setState(e.target.value); setOffset(0); }}><option value="">{t("web.pub.all_states")}</option>{STATES.map(s => <option key={s} value={s}>{t(`web.pub.status.${s}`)}</option>)}</select>
      <span>{`${t("web.pub.count")}: ${list.data?.total ?? 0}`}</span>
    </div>
    {list.data?.total === 0 && <p>{t("web.pub.empty")}</p>}
    {list.data?.items.map(record => <section className="panel stack" key={record.id}>
      <div className="row"><strong>{record.content.title}</strong><span>{t(`web.pub.status.${record.status}`)}</span><span>{targets.data?.filter(x => x.id === record.target_id).map(x => `${x.platform} / ${x.account} / ${x.channel}`).join("")}</span></div>
      <p>{record.content.body}</p><p className="small muted">{Object.values(record.content.classification).filter(Boolean).join(" / ")}</p><div className="row">{record.content.items.map((item, i) => <Link key={`${i}:${item.asset_id}`} to={`/library?search=${item.asset_id}`}>{item.asset_id.slice(0, 8)}</Link>)}</div>
      {record.url && <a href={record.url} target="_blank" rel="noreferrer">{t("web.pub.open")}</a>}
      {record.actual_published_at && <time>{record.actual_published_at}</time>}
      <div className="row"><button onClick={() => { setEdit(record); setClassification(record.content.classification); setTitle(record.content.title); setBody(record.content.body); setTags(record.content.tags.join(", ")); setSelected(record.content.items.map(i => i.asset_id)); setDraftTarget(record.target_id); setDraftCategory(record.category_id ?? ""); }}>{t("web.pub.edit")}</button>
        {NEXT[record.status]?.map(s => <button key={s} onClick={() => { setTransition({ record, status: s }); setUrl(record.url); setActual(""); setEvidence(""); setExternal(""); }}>{t(`web.pub.status.${s}`)}</button>)}</div>
      <details><summary>{t("web.pub.history")}</summary>{record.revisions.map(r => <p key={r.revision}>{`${r.revision} · ${r.title} · ${r.items.map(i => i.asset_id.slice(0, 8)).join(", ")}`}</p>)}{record.events.map(e => <p key={e.id}>{`${e.created_at} · ${t(`web.pub.status.${e.event}`)}`}</p>)}</details>
    </section>)}
    <div className="row"><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 30))}>{t("web.pub.prev")}</button><button disabled={offset + 30 >= (list.data?.total ?? 0)} onClick={() => setOffset(offset + 30)}>{t("web.pub.next")}</button></div>
    {transition && <section className="panel stack" role="dialog" aria-label={t("web.pub.confirm")}>
      <h2>{t("web.pub.confirm")}</h2><p>{t(`web.pub.status.${transition.status}`)}</p>
      {transition.status === "scheduled" && <label>{t("web.pub.scheduled_time")}<input type="datetime-local" value={actual} onChange={e => setActual(e.target.value)} /></label>}
      {transition.status === "published" && <><label>{t("web.pub.url")}<input value={url} onChange={e => setUrl(e.target.value)} /></label><label>{t("web.pub.external")}<input value={external} onChange={e => setExternal(e.target.value)} /></label><label>{t("web.pub.actual")}<input type="datetime-local" value={actual} onChange={e => setActual(e.target.value)} /></label></>}
      <label>{t("web.pub.evidence")}<textarea value={evidence} onChange={e => setEvidence(e.target.value)} /></label>
      <div className="row"><button disabled={change.isPending || ((transition.status === "published" && (!actual || !evidence)) || (transition.status === "scheduled" && !actual))} onClick={() => change.mutate()}>{t("web.pub.confirm")}</button><button onClick={() => setTransition(null)}>{t("web.pub.cancel")}</button></div>
    </section>}
  </div>;
}
