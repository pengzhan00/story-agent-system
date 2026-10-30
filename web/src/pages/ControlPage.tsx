import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { post, put } from "../api/client";
import { controlGet, type Capability, type ControlRun, type Plan, type PlanNode } from "../api/control";
import { useAllAssets } from "../api/library";
import { ErrorNote } from "../components/ErrorNote";
import { mediaOfAsset } from "../lib/assets";
import { useT } from "../i18n";

export function ControlPage() {
  const t = useT();
  const qc = useQueryClient();
  const [search] = useSearchParams();
  const pid = Number(search.get("project")) || undefined;
  const assets = useAllAssets();
  const capabilities = useQuery({ queryKey: ["capabilities", pid], queryFn: () => controlGet<Capability[]>(`/api/capabilities${pid ? `?project_id=${pid}` : ""}`) });
  const runs = useQuery({ queryKey: ["control-runs"], queryFn: () => controlGet<{ total: number; items: ControlRun[] }>("/api/control/runs"), refetchInterval: 2500 });
  const templates = useQuery({ queryKey: ["control-templates"], queryFn: () => controlGet<{ id: string; version: number; name: string; definition: Plan }[]>("/api/control/templates") });
  const [name, setName] = useState("");
  const [script, setScript] = useState("");
  const [song, setSong] = useState("");
  const [assemble, setAssemble] = useState(false);
  const [freezeTail, setFreezeTail] = useState(false);
  const [separate, setSeparate] = useState(true);
  const scripts = useQuery({ queryKey: ["control-scripts", pid], enabled: Boolean(pid), queryFn: () => controlGet<{ id: number; title: string }[]>(`/api/projects/${pid}/scripts`) });
  const fromProject = useMutation({ mutationFn: () => post<Plan>(`/api/projects/${pid}/control/plan`, { script_id: Number(script), source_audio: song, assemble, tail_policy: freezeTail ? "freeze_last" : "reject", separate_vocals: separate }), onSuccess: x => { setName(x.name); setNodes(x.nodes); setPreview(null); setTemplateBase(null); } });
  const [templateBase, setTemplateBase] = useState<{ id: string; version: number } | null>(null);
  const [externalIds, setExternalIds] = useState<Record<string, string>>({});
  const [nodes, setNodes] = useState<PlanNode[]>([]);
  const [preview, setPreview] = useState<{ digest?: string } | null>(null);
  const [previewKey, setPreviewKey] = useState("");
  const [requestKey, setRequestKey] = useState(() => crypto.randomUUID());
  const plan = (): Plan => ({ name, project_id: pid, nodes });
  const submit = useMutation({ mutationFn: () => post("/api/control/runs", { ...plan(), request_key: requestKey, expected_digest: preview?.digest }), onSuccess: () => { void qc.invalidateQueries({ queryKey: ["control-runs"] }); setPreview(null); } });
  const check = useMutation({ mutationFn: (body: Plan) => post<{ digest?: string }>("/api/control/plans", body), onSuccess: (data, body) => { setPreview(data); setPreviewKey(JSON.stringify(body)); setRequestKey(crypto.randomUUID()); } });
  const save = useMutation({ mutationFn: () => templateBase ? put<{ id: string; version: number }>(`/api/control/templates/${templateBase.id}`, { ...plan(), template_version: templateBase.version }) : post<{ id: string; version: number }>("/api/control/templates", plan()), onSuccess: x => { setTemplateBase(x); void qc.invalidateQueries({ queryKey: ["control-templates"] }); } });
  const action = useMutation({ mutationFn: ({ id, ...body }: { id: string; action: string; node_id?: string; revision?: number; external_id?: string; evidence?: string }) => post(`/api/control/runs/${id}/actions`, body), onSuccess: () => { void qc.invalidateQueries({ queryKey: ["control-runs"] }); } });
  const edit = (index: number, patch: Partial<PlanNode>) => { setPreview(null); setNodes(old => old.map((n, i) => i === index ? { ...n, ...patch } : n)); };
  const add = () => {
    const cap = capabilities.data?.find(c => c.id === "image.generate") ?? capabilities.data?.[0];
    if (cap) { setPreview(null); setNodes(old => [...old, { id: `step_${crypto.randomUUID().slice(0, 8)}`, kind: cap.id, model_ref: cap.bindings.find(b => b.provider !== "control-tools")?.id ?? "", inputs: {}, params: {}, review: false }]); }
  };
  return <div className="stack">
    <h1>{t("web.control.title")}</h1>
    <p className="muted">{t("web.control.note")}</p>
    <ErrorNote error={fromProject.error ?? scripts.error ?? capabilities.error ?? runs.error ?? templates.error ?? check.error ?? submit.error ?? save.error ?? action.error} />
    {pid && <section className="panel stack"><h2>{t("web.control.from_project")}</h2>
      <label>{t("web.control.script")}<select value={script} onChange={e => setScript(e.target.value)}><option value="">{t("web.pub.pick")}</option>{scripts.data?.map(x => <option key={x.id} value={x.id}>{`${x.id} · ${x.title}`}</option>)}</select></label>
      <label>{t("web.control.source_song")}<select value={song} onChange={e => setSong(e.target.value)}><option value="">{t("web.pub.none")}</option>{assets.data?.filter(a => mediaOfAsset(a) === "audio" && !a.locked && !a.trashed_at).map(a => <option key={a.id} value={`asset:${a.id}`}>{`${a.kind} · ${a.id.slice(0, 8)}`}</option>)}</select></label>
      <label><input type="checkbox" checked={separate} onChange={e => setSeparate(e.target.checked)} />{t("web.control.separate")}</label>
      <label><input type="checkbox" checked={assemble} onChange={e => setAssemble(e.target.checked)} />{t("web.control.assemble_locked")}</label>
      <label><input type="checkbox" checked={freezeTail} onChange={e => setFreezeTail(e.target.checked)} />{t("web.control.freeze_tail")}</label>
      <button disabled={!script || fromProject.isPending || (assemble && !song)} onClick={() => fromProject.mutate()}>{t("web.control.from_project")}</button>
    </section>}
    <section className="panel stack">
      <label>{t("web.control.name")}<input value={name} onChange={e => { setName(e.target.value); setPreview(null); }} /></label>
      <div className="row"><button onClick={add} disabled={!capabilities.data}>{t("web.control.add")}</button>
        <select aria-label={t("web.control.template")} value="" onChange={e => { const template = templates.data?.find(x => `${x.id}:${x.version}` === e.target.value); if (template) { setTemplateBase({ id: template.id, version: template.version }); setName(template.definition.name); setNodes(template.definition.nodes); setPreview(null); } }}>
          <option value="">{t("web.control.template")}</option>{templates.data?.map(x => <option key={`${x.id}:${x.version}`} value={`${x.id}:${x.version}`}>{`${x.name || x.id.slice(0, 8)} · ${x.version}`}</option>)}
        </select>
      </div>
      {!nodes.length && <p>{t("web.control.empty")}</p>}
      {nodes.map((node, index) => {
        const cap = capabilities.data?.find(c => c.id === node.kind);
        return <fieldset className="stack" key={node.id}><legend>{node.id}</legend>
          <div className="row">
            <select aria-label={t("web.control.capability")} value={node.kind} onChange={e => { const next = capabilities.data?.find(c => c.id === e.target.value); edit(index, { kind: e.target.value, model_ref: next?.bindings.find(b => b.provider !== "control-tools")?.id ?? "", inputs: {}, params: e.target.value === "video.lipsync" ? { duration: 5 } : {} }); }}>
              {capabilities.data?.filter(c => (c.id === node.kind || c.inputs.every(f => !f.required || ["media", "text", "longtext", "bool"].includes(f.type)))).map(c => <option key={c.id} value={c.id}>{t(c.label_key)}</option>)}
            </select>
            <select aria-label={t("web.control.binding")} value={node.model_ref} onChange={e => edit(index, { model_ref: e.target.value })}>
              {cap?.bindings.filter(b => b.provider !== "control-tools" || b.id === node.model_ref).map(b => <option key={b.id} value={b.id}>{`${b.id} · ${b.provider} · ${t("web.control.configured")}`}</option>)}
            </select>
            <button onClick={() => { setPreview(null); setNodes(old => old.filter((_, i) => i !== index)); }}>{t("web.control.remove")}</button>
          </div>
          {node.kind.startsWith("video.") && ["video.generate", "video.av"].includes(node.kind) && <label>{t("web.control.mode")}<select value={String(node.params.mode ?? "t2v")} onChange={e => edit(index, { params: { ...node.params, mode: e.target.value } })}>
            <option value="t2v">{t("web.control.t2v")}</option><option value="i2v">{t("web.control.i2v")}</option>
          </select></label>}
          {node.kind === "video.lipsync" && <div className="row">
            <label>{t("web.control.seconds")}<input type="number" min="0.1" max="360" step="0.1" value={Number(node.params.duration ?? 5)} onChange={e => edit(index, { params: { ...node.params, duration: Number(e.target.value) } })} /></label>
            <label>{t("web.control.audio_start")}<input type="number" min="0" step="0.001" value={Number(node.params.audio_start_time ?? 0)} onChange={e => edit(index, { params: { ...node.params, audio_start_time: Number(e.target.value), duration: node.params.duration ?? 5 } })} /></label>
          </div>}
          {node.kind === "audio.music" && <label>{t("web.control.music_task")}<select value={String(node.params.task_type === "text2music" ? "write" : node.params.task_type ?? node.params.music_task ?? "write")} onChange={e => edit(index, { params: node.model_ref.includes("yue") ? { music_task: e.target.value } : { task_type: e.target.value === "write" ? "text2music" : e.target.value } })}>
            <option value="write">{t("web.control.music_write")}</option><option value="cover">{t("web.control.music_cover")}</option>
            {!node.model_ref.includes("yue") && <option value="repaint">{t("web.control.music_repaint")}</option>}
          </select></label>}
          {node.kind === "audio.music" && node.params.task_type === "repaint" && <div className="row">
            <label>{t("web.control.audio_start")}<input type="number" min="0" value={Number(node.params.repainting_start ?? 0)} onChange={e => edit(index, { params: { ...node.params, repainting_start: Number(e.target.value) } })} /></label>
            <label>{t("web.control.audio_end")}<input type="number" min="0" value={Number(node.params.repainting_end ?? 0)} onChange={e => edit(index, { params: { ...node.params, repainting_end: Number(e.target.value) } })} /></label>
          </div>}
          {cap?.inputs.map(field => {
            const value = node.inputs[field.name];
            if (field.type === "media") {
              const selected = typeof value === "object" && value !== null ? JSON.stringify(value) : String(value ?? "");
              return <label key={field.name}>{t(`workshop.field.${field.name}`)}{field.required ? " *" : ""}<select value={selected} onChange={e => edit(index, { inputs: { ...node.inputs, [field.name]: e.target.value.startsWith("{") ? JSON.parse(e.target.value) : e.target.value } })}>
                <option value="">{t("web.control.pick_asset")}</option>
                {nodes.slice(0, index).flatMap(parent => Object.entries(capabilities.data?.find(c => c.id === parent.kind)?.outputs ?? {}).filter(([, media]) => media === field.media).map(([port]) => <option key={`${parent.id}:${port}`} value={JSON.stringify({ $node: parent.id, port })}>{`${parent.id} → ${port}`}</option>))}
                {assets.data?.filter(a => mediaOfAsset(a) === field.media && !a.trashed_at && !a.locked).map(a => <option key={a.id} value={`asset:${a.id}`}>{`${a.kind} · ${a.id.slice(0, 8)}`}</option>)}
              </select></label>;
            }
            if (field.type === "shots") return <div key={field.name} className="stack"><strong>{t("web.control.assembly_parts")}</strong>{Array.isArray(value) && (value as { video: unknown; seconds: number }[]).map((part, i) => <label key={i}>{`${t("web.control.shot")} ${i + 1}`}<input type="number" min="0.01" step="0.001" value={part.seconds} onChange={e => edit(index, { inputs: { ...node.inputs, [field.name]: (value as Record<string, unknown>[]).map((old, j) => j === i ? { ...old, seconds: Number(e.target.value) } : old) } })} /></label>)}</div>;
            if (field.type === "bool") return <label key={field.name}><input type="checkbox" checked={Boolean(value)} onChange={e => edit(index, { inputs: { ...node.inputs, [field.name]: e.target.checked } })} />{t(`workshop.field.${field.name}`)}</label>;
            if (["text", "longtext"].includes(field.type)) return <label key={field.name}>{t(`workshop.field.${field.name}`)}<textarea value={String(value ?? "")} onChange={e => edit(index, { inputs: { ...node.inputs, [field.name]: e.target.value } })} /></label>;
            return null;
          })}
          <label><input type="checkbox" checked={node.review} onChange={e => edit(index, { review: e.target.checked })} />{t("web.control.review")}</label>
        </fieldset>;
      })}
      <div className="row"><button disabled={!nodes.length || check.isPending} onClick={() => check.mutate(plan())}>{t("web.control.preview")}</button>
        <button disabled={!nodes.length || save.isPending} onClick={() => save.mutate()}>{t("web.control.save")}</button>
        <button className="primary" disabled={!preview || previewKey !== JSON.stringify(plan()) || submit.isPending} onClick={() => submit.mutate()}>{t("web.control.submit")}</button></div>
      {preview != null && <p className="good">{t("web.control.valid")}</p>}
    </section>
    <h2>{t("web.control.runs")}</h2>
    {runs.data?.items.map(run => <section className="panel stack" key={run.id}>
      <div className="row"><strong>{run.name || run.id.slice(0, 8)}</strong><span>{t(`web.control.status.${run.status}`)}</span>
        {!["succeeded", "failed", "cancelled"].includes(run.status) && <button disabled={action.isPending} onClick={() => action.mutate({ id: run.id, action: "cancel" })}>{t("web.control.cancel")}</button>}</div>
      {run.nodes.map(n => <div className="row" key={n.node_id}><code>{n.node_id}</code><span>{t(`web.control.status.${n.status}`)}</span>
        {n.reason && <span className="bad">{t(`web.control.reason.${n.reason}`)}</span>}{n.job_id && <Link to={`/queue?job=${n.job_id}`}>{t("web.control.job")}</Link>}
        {n.status === "waiting_review" && <button disabled={action.isPending} onClick={() => action.mutate({ id: run.id, action: "approve", node_id: n.node_id, revision: n.revision })}>{t("web.control.approve")}</button>}
        {n.reason === "reconciliation_required" && <><input aria-label={t("web.control.external_id")} value={externalIds[`${run.id}:${n.node_id}`] ?? ""} onChange={e => setExternalIds(old => ({ ...old, [`${run.id}:${n.node_id}`]: e.target.value }))} /><button disabled={action.isPending || !externalIds[`${run.id}:${n.node_id}`]} onClick={() => { if (window.confirm(t("web.control.reconcile_confirm"))) action.mutate({ id: run.id, action: "reconcile", node_id: n.node_id, revision: n.revision, external_id: externalIds[`${run.id}:${n.node_id}`], evidence: t("web.control.reconcile_confirm") }); }}>{t("web.control.reconcile")}</button></>}
        {["failed", "blocked"].includes(n.status) && n.reason !== "reconciliation_required" && <button disabled={action.isPending} onClick={() => action.mutate({ id: run.id, action: "retry", node_id: n.node_id, revision: n.revision })}>{t("web.control.retry")}</button>}
        {Object.values(n.outputs).map(a => <Link key={a} to={`/library?search=${a}`}>{a.slice(0, 8)}</Link>)}
      </div>)}
    </section>)}
  </div>;
}
