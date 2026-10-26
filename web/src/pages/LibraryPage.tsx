import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useProjects } from "../api/hooks";
import { useScripts } from "../api/scripts";
import { useAllAssets, useLibraryActions, useLibraryItems, useLibraryRoles, useTrashedAssets } from "../api/library";
import type { Asset, LibraryItem } from "../api/types";
import { useWorkshop } from "../api/workshop";
import { AssetPreview } from "../components/Candidates";
import { ErrorNote } from "../components/ErrorNote";
import { MakeProject } from "../components/MakeProject";
import { ProjectScripts } from "../components/ProjectScripts";
import { useT } from "../i18n";
import { duration } from "../lib/format";
import { CATEGORIES, type Category, categoryOf, mediaOfAsset, targetsFor } from "../lib/assets";

const NAMED = "named";
const WORKSHOP = "workshop";
const SCRIPTS = "project_scripts";
const TRASHED = "trashed";

function CopyButton({ text, label }: { text: string; label: string }) {
  const t = useT();
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="chip chip-button"
      title={text}
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(
          () => {
            setDone(true);
            window.setTimeout(() => setDone(false), 1500);
          },
          () => undefined,
        );
      }}
    >
      {done ? t("web.library.copied") : label}
    </button>
  );
}

/** The ways a result can be referred to: paste any of these into an input field, a pipeline, the CLI or the API. */
function RefLine({ id, named }: { id: string; named: string[] }) {
  const t = useT();
  return (
    <div className="stack small">
      <div className="row">
        <span className="muted">{t("web.library.id")}</span>
        <code>{id}</code>
        <CopyButton text={id} label={t("web.library.copy_id")} />
      </div>
      <div className="row">
        <span className="muted">{t("web.library.ref")}</span>
        <code>{`asset:${id}`}</code>
        <CopyButton text={`asset:${id}`} label={t("web.library.copy_ref")} />
      </div>
      {named.map((r) => (
        <div key={r} className="row">
          <span className="muted">{t("web.library.named_ref")}</span>
          <code>{r}</code>
          <CopyButton text={r} label={t("web.library.copy_ref")} />
        </div>
      ))}
    </div>
  );
}

function FileAs({ asset, roles, onDone }: { asset: Asset; roles: string[]; onDone: () => void }) {
  const t = useT();
  const { publish } = useLibraryActions();
  const [role, setRole] = useState(roles[0] ?? "");
  const [name, setName] = useState("");
  const [text, setText] = useState("");
  const [consent, setConsent] = useState(false);
  const voice = role === "voice";
  return (
    <div className="stack">
      <div className="form-grid">
        <label className="field">
          <span>{t("web.library.role")}</span>
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            {roles.map((r) => (
              <option key={r} value={r}>
                {t(`library.role.${r}`)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>{t("web.library.name")}</span>
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        {voice ? (
          <>
            <label className="field wide">
              <span>{t("web.library.voice_text")}</span>
              <input value={text} onChange={(e) => setText(e.target.value)} />
            </label>
            <label className="field check">
              <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
              <span>{t("web.library.voice_consent")}</span>
            </label>
          </>
        ) : null}
      </div>
      <div className="row">
        <button
          className="primary"
          disabled={!name.trim() || publish.isPending}
          onClick={() =>
            publish.mutate(
              { asset_id: asset.id, role, name: name.trim(), ...(voice ? { meta: { text, consent } } : {}) },
              { onSuccess: onDone },
            )
          }
        >
          {t("web.library.file_it")}
        </button>
        <span className="muted small">{`library:${role}/${name.trim() || "…"}`}</span>
      </div>
      <ErrorNote error={publish.error} />
    </div>
  );
}

function AssetCard({
  asset, source, named, roles, modules, selected, onToggleSelect,
}: {
  asset: Asset;
  source: string;
  named: string[];
  roles: string[];
  modules: ReturnType<typeof useWorkshop>["data"];
  selected: boolean;
  onToggleSelect: (id: string) => void;
}) {
  const t = useT();
  const { trash, purge } = useLibraryActions();
  const [filing, setFiling] = useState(false);
  const cat = categoryOf(asset);
  const targets = targetsFor(modules ?? [], mediaOfAsset(asset));
  const model = (asset.provenance?.["model"] as { ref?: string } | undefined)?.ref ?? "";
  return (
    <li className="card stack">
      <div className="row">
        <input type="checkbox" aria-label={t("web.library.select")} checked={selected} onChange={() => onToggleSelect(asset.id)} />
        <span className="chip">{t(`library.cat.${cat}`)}</span>
        <span className="muted small">{source}</span>
        <span className="spacer" />
        <span className="muted small">{asset.created_at.replace("T", " ").slice(0, 16)}</span>
      </div>
      <AssetPreview asset={asset} />
      <div className="row small muted">
        {model ? <span>{model}</span> : null}
        {asset.metadata?.["seed"] !== undefined ? <span>{t("web.creative.seed_n", { seed: String(asset.metadata["seed"]) })}</span> : null}
        {asset.duration_sec ? <span>{duration(asset.duration_sec)}</span> : null}
        {asset.width && asset.height ? <span>{`${asset.width}×${asset.height}`}</span> : null}
      </div>
      <RefLine id={asset.id} named={named} />
      {targets.length > 0 ? (
        <div className="row small">
          <span className="muted">{t("web.workshop.use_as")}</span>
          {targets.map((tg) => (
            <Link key={`${tg.module}${tg.kind}`} className="chip chip-button" to={`/workshop?use=${tg.module}:${tg.kind}:${tg.field}:${asset.id}`}>
              {`${t(`workshop.module.${tg.module}`)} · ${t(`media.kind.${tg.kind}`)}`}
            </Link>
          ))}
        </div>
      ) : null}
      {cat === "story" || cat === "script" || asset.kind === "upload.text" ? <MakeProject assetId={asset.id} /> : null}
      <div className="row small">
        <button className="link" onClick={() => setFiling(!filing)}>
          {t("web.library.file_as")}
        </button>
        <span className="spacer" />
        <button
          className="link"
          disabled={trash.isPending}
          onClick={() => {
            if (window.confirm(t("web.library.trash_confirm"))) trash.mutate(asset.id);
          }}
        >
          {t("web.library.trash")}
        </button>
        {!asset.is_current ? (
          <button
            className="link"
            disabled={purge.isPending}
            onClick={() => {
              if (window.confirm(t("web.creative.purge_confirm"))) purge.mutate(asset.id);
            }}
          >
            {t("web.creative.purge")}
          </button>
        ) : null}
      </div>
      {filing ? <FileAs asset={asset} roles={roles} onDone={() => setFiling(false)} /> : null}
      <ErrorNote error={trash.error} />
    </li>
  );
}

function NamedCard({ item }: { item: LibraryItem }) {
  const t = useT();
  const { remove } = useLibraryActions();
  return (
    <li className="card stack">
      <div className="row">
        <span className="chip">{t(`library.role.${item.role}`)}</span>
        <strong>{item.name}</strong>
        <span className="spacer" />
        <span className="muted small">{item.created_at.replace("T", " ").slice(0, 16)}</span>
      </div>
      <AssetPreview asset={item.asset} />
      {item.description ? <p className="small">{item.description}</p> : null}
      <div className="stack small">
        <div className="row">
          <span className="muted">{t("web.library.named_ref")}</span>
          <code>{item.ref}</code>
          <CopyButton text={item.ref} label={t("web.library.copy_ref")} />
        </div>
        <div className="row">
          <span className="muted">{t("web.library.id")}</span>
          <code>{item.asset.id}</code>
          <CopyButton text={`asset:${item.asset.id}`} label={t("web.library.copy_ref")} />
        </div>
      </div>
      <div className="row small">
        <span className="spacer" />
        <button
          className="link"
          disabled={remove.isPending}
          onClick={() => {
            if (window.confirm(t("web.library.remove_confirm", { ref: item.ref }))) remove.mutate(item.id);
          }}
        >
          {t("web.library.remove")}
        </button>
      </div>
      <ErrorNote error={remove.error} />
    </li>
  );
}

function TrashedCard({ asset, source }: { asset: Asset; source: string }) {
  const t = useT();
  const { restore, purge } = useLibraryActions();
  return (
    <li className="card stack">
      <div className="row">
        <span className="chip">{t(`library.cat.${categoryOf(asset)}`)}</span>
        <span className="muted small">{source}</span>
        <span className="spacer" />
        <span className="muted small">{asset.trashed_at?.replace("T", " ").slice(0, 16)}</span>
      </div>
      <AssetPreview asset={asset} />
      <div className="row small">
        <span className="muted">{asset.kind}</span>
        <code className="small">{asset.id.slice(0, 8)}</code>
        <span className="spacer" />
        <button className="link" disabled={restore.isPending} onClick={() => restore.mutate(asset.id)}>
          {t("web.library.restore")}
        </button>
        <button
          className="link"
          disabled={purge.isPending}
          onClick={() => {
            if (window.confirm(t("web.creative.purge_confirm"))) purge.mutate(asset.id);
          }}
        >
          {t("web.creative.purge")}
        </button>
      </div>
      <ErrorNote error={restore.error ?? purge.error} />
    </li>
  );
}

function HowTo() {
  const t = useT();
  const lines = ["input", "named", "pipeline", "lora", "cli", "api"] as const;
  return (
    <details className="card">
      <summary className="strong">{t("web.library.how_title")}</summary>
      <p className="muted small">{t("web.library.how_intro")}</p>
      <dl className="small">
        {lines.map((l) => (
          <div key={l} className="stack">
            <dt className="strong">{t(`web.library.how.${l}`)}</dt>
            <dd>
              <code>{t(`web.library.how.${l}_example`)}</code>
            </dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

/** Everything that was made or uploaded, by kind, each with the ID and the exact text that calls it from elsewhere. */
export function LibraryPage() {
  const t = useT();
  const assets = useAllAssets();
  const trashed = useTrashedAssets();
  const items = useLibraryItems();
  const roles = useLibraryRoles();
  const projects = useProjects();
  const shop = useWorkshop();
  const [tab, setTab] = useState<string>("all");
  const [scope, setScope] = useState<string>("");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const all = assets.data ?? [];
  // library copies are listed under "named"; their asset copies stay out of the kind tabs
  const copies = useMemo(() => new Set((items.data ?? []).map((i) => i.asset.id)), [items.data]);
  const mine = useMemo(() => all.filter((a) => !copies.has(a.id)), [all, copies]);
  const projectName = (id: number | null) => (id === null ? t("web.library.workshop") : (projects.data?.find((p) => p.id === id)?.name ?? `#${id}`));
  const scoped = mine.filter((a) => (scope === "" ? true : scope === WORKSHOP ? a.project_id === null : String(a.project_id) === scope));
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: scoped.length };
    for (const a of scoped) c[categoryOf(a)] = (c[categoryOf(a)] ?? 0) + 1;
    return c;
  }, [scoped]);
  const scriptList = useScripts("", null);
  const needle = q.trim().toLowerCase();
  const shown = scoped.filter((a) => (tab === "all" || categoryOf(a) === (tab as Category)) && (!needle || a.id.includes(needle) || a.kind.includes(needle) || a.slot.toLowerCase().includes(needle)));
  const namedBy = (assetId: string) => (items.data ?? []).filter((i) => i.source_asset_id === assetId).map((i) => i.ref);
  const namedItems = (items.data ?? []).filter((i) => !needle || i.ref.toLowerCase().includes(needle));
  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const toggleAll = () => {
    setSelected((prev) => (prev.size === shown.length ? new Set() : new Set(shown.map((a) => a.id))));
  };
  const selCount = shown.filter((a) => selected.has(a.id)).length;
  const batch = useLibraryActions();
  return (
    <div className="stack">
      <h1>{t("web.library.title")}</h1>
      <p className="muted">{t("web.library.intro")}</p>
      <HowTo />
      <ErrorNote error={assets.error ?? items.error} />
      <nav className="tabs">
        {["all", ...CATEGORIES].map((c) => (
          <button key={c} className={tab === c ? "tab active" : "tab"} onClick={() => setTab(c)}>
            {`${t(c === "all" ? "web.library.all" : `library.cat.${c}`)} (${counts[c] ?? 0})`}
          </button>
        ))}
        <button className={tab === SCRIPTS ? "tab active" : "tab"} onClick={() => setTab(SCRIPTS)}>
          {`${t("web.library.scripts.tab")} (${scriptList.data?.length ?? 0})`}
        </button>
        <button className={tab === NAMED ? "tab active" : "tab"} onClick={() => setTab(NAMED)}>
          {`${t("web.library.named")} (${items.data?.length ?? 0})`}
        </button>
        <button className={tab === TRASHED ? "tab active" : "tab"} onClick={() => setTab(TRASHED)}>
          {`${t("web.library.trash_tab")} (${(trashed.data ?? []).filter((a) => a.trashed_at).length})`}
        </button>
      </nav>
      <div className="row">
        {tab !== NAMED && tab !== SCRIPTS ? (
          <label className="row small">
            <span>{t("web.library.scope")}</span>
            <select value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="">{t("web.library.scope_all")}</option>
              <option value={WORKSHOP}>{t("web.library.workshop")}</option>
              {(projects.data ?? []).map((p) => (
                <option key={p.id} value={String(p.id)}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <input type="search" value={q} placeholder={t("web.library.search")} aria-label={t("web.library.search")} onChange={(e) => setQ(e.target.value)} />
      </div>
      {shown.length > 0 ? (
        <div className="row small">
          <button className="link" onClick={toggleAll}>
            {selCount === shown.length ? t("web.library.unselect_all") : t("web.library.select_all")}
          </button>
          {selCount > 0 ? (
            <>
              <span className="muted">{t("web.library.selected_n", { n: selCount })}</span>
              <button className="link" disabled={batch.trashMany.isPending} onClick={() => {
                if (window.confirm(t("web.library.trash_many_confirm", { n: selCount }))) batch.trashMany.mutate([...selected]);
              }}>
                {t("web.library.trash")}
              </button>
              <button className="link" disabled={batch.purgeMany.isPending} onClick={() => {
                if (window.confirm(t("web.library.purge_many_confirm", { n: selCount }))) batch.purgeMany.mutate([...selected]);
              }}>
                {t("web.creative.purge")}
              </button>
            </>
          ) : null}
          {scope !== "" && scope !== WORKSHOP ? (
            <button className="link" disabled={batch.purgeProject.isPending} onClick={() => {
              const pid = Number(scope);
              if (Number.isFinite(pid) && window.confirm(t("web.library.purge_project_confirm", { n: shown.length }))) {
                batch.purgeProject.mutate({ projectId: pid, includeCurrent: false });
              }
            }}>
              {t("web.library.purge_project")}
            </button>
          ) : null}
          <ErrorNote error={batch.trashMany.error ?? batch.purgeMany.error ?? batch.purgeProject.error} />
        </div>
      ) : null}
      {tab === SCRIPTS ? (
        <ProjectScripts q={q} projectId={null} />
      ) : tab === NAMED ? (
        namedItems.length === 0 ? (
          <p className="empty">{t("web.library.empty_named")}</p>
        ) : (
          <ul className="grid">
            {namedItems.map((i) => (
              <NamedCard key={i.id} item={i} />
            ))}
          </ul>
        )
      ) : tab === TRASHED ? (
        (trashed.data ?? []).filter((a) => a.trashed_at).length === 0 ? (
          <p className="empty">{t("web.library.empty_trash")}</p>
        ) : (
          <ul className="grid">
            {(trashed.data ?? []).filter((a) => a.trashed_at).map((a) => (
              <TrashedCard key={a.id} asset={a} source={projectName(a.project_id)} />
            ))}
          </ul>
        )
      ) : shown.length === 0 ? (
        <p className="empty">{t("web.library.empty")}</p>
      ) : (
        <ul className="grid">
          {shown.map((a) => (
            <AssetCard key={a.id} asset={a} source={projectName(a.project_id)} named={namedBy(a.id)} roles={roles.data ?? []} modules={shop.data} selected={selected.has(a.id)} onToggleSelect={toggleSelect} />
          ))}
        </ul>
      )}
    </div>
  );
}
