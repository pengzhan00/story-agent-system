import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, put } from "../api/client";
import { useProjects } from "../api/hooks";
import { useT } from "../i18n";
import { ErrorNote } from "./ErrorNote";

function AdoptForm({ id, media }: { id: string; media: string }) {
  const t = useT();
  const qc = useQueryClient();
  const projects = useProjects();
  const [project, setProject] = useState("");
  const [shot, setShot] = useState("");
  const slot = media === "image" ? "shot.keyframe" : "shot.video";
  const shots = useQuery({ queryKey: ["adopt-shots", project], enabled: Boolean(project), queryFn: () => api<{ id: number; shot_number: number }[]>(`/api/projects/${project}/shots`) });
  const selected = useQuery({ queryKey: ["asset-selection", shot, slot], enabled: Boolean(shot), queryFn: () => api<{ revision: number }>(`/api/control/selections?owner_type=shot&owner_id=${shot}&slot=${slot}`) });
  const save = useMutation({ mutationFn: () => put("/api/control/selections", { owner_type: "shot", owner_id: shot, slot, asset_id: id, revision: selected.data?.revision ?? 0 }), onSuccess: () => { void qc.invalidateQueries({ queryKey: ["asset-selection", shot, slot] }); void qc.invalidateQueries({ queryKey: ["assets"] }); } });
  return <div className="stack">
    <ErrorNote error={projects.error ?? shots.error ?? selected.error ?? save.error} />
    <label>{t("web.control.project")}<select value={project} onChange={e => { setProject(e.target.value); setShot(""); }}><option value="">{t("web.pub.pick")}</option>{projects.data?.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
    <label>{t("web.control.shot")}<select value={shot} onChange={e => setShot(e.target.value)}><option value="">{t("web.pub.pick")}</option>{shots.data?.map(s => <option key={s.id} value={s.id}>{s.shot_number}</option>)}</select></label>
    <button disabled={!shot || !selected.data || save.isPending} onClick={() => save.mutate()}>{t("web.control.adopt")}</button>
    {save.isSuccess && <span>{t("web.control.adopted")}</span>}
  </div>;
}

export function AdoptToShot({ id, media }: { id: string; media: string }) {
  const [open, setOpen] = useState(false);
  const t = useT();
  if (!["image", "video", "compose"].includes(media)) return null;
  return <div className="stack"><button onClick={() => setOpen(!open)}>{t("web.control.adopt")}</button>{open && <AdoptForm id={id} media={media} />}</div>;
}
