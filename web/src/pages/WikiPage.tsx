import { useMemo, type ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useWikiList, useWikiPage, useWikiSync } from "../api/wiki";
import type { WikiBrief } from "../api/types";
import { ErrorNote } from "../components/ErrorNote";
import { MiniGraph, WikiGraphView } from "../components/WikiGraphView";
import { useT } from "../i18n";
import { Markdown, headingsOf, readBlocks } from "../lib/markdown";

const TOP_TAGS = 14;

const hrefOf = (slug: string) => `/wiki/${slug}`;

function PageRow({ page, active }: { page: WikiBrief; active: boolean }) {
  const t = useT();
  return (
    <li className={active ? "wiki-row active" : "wiki-row"}>
      <Link to={hrefOf(page.slug)} aria-current={active ? "page" : undefined}>
        <span className="strong">{page.title}</span>
        <span className="row small">
          <span className="chip">{page.source}</span>
          {page.rating !== "general" ? <span className="chip chip-stale">{t("web.wiki.restricted")}</span> : null}
        </span>
        <span className="muted small">{page.snippet || page.summary}</span>
      </Link>
    </li>
  );
}

function Reader({ slug, search = "", mini = true }: { slug: string; search?: string; mini?: boolean }) {
  const t = useT();
  const page = useWikiPage(slug);
  const data = page.data;
  const tagHref = (tag: string) => {
    if (!search) return `/wiki?tag=${encodeURIComponent(tag)}`;
    const next = new URLSearchParams(search);
    next.set("tag", tag);
    return `${hrefOf(slug)}?${next.toString()}`;
  };
  const toc = useMemo(() => (data ? headingsOf(readBlocks(data.body, data.page.title)).filter((h) => h.level <= 3) : []), [data]);
  if (page.isLoading) return <p className="muted">{t("web.loading")}</p>;
  if (page.error) return <ErrorNote error={page.error} />;
  if (!data) return null;
  return (
    <article className="card wiki-reader">
      <header className="stack">
        <h2>{data.page.title}</h2>
        <div className="row small muted">
          <span className="chip">{data.page.source}</span>
          <code>{data.page.path}</code>
          {data.page.updated ? <span>{t("web.wiki.updated", { when: data.page.updated.slice(0, 10) })}</span> : null}
        </div>
        {data.page.tags.length ? (
          <div className="row">
            {data.page.tags.map((tag) => (
              <Link key={tag} className="chip chip-button" to={tagHref(tag)}>
                {tag}
              </Link>
            ))}
          </div>
        ) : null}
      </header>
      {toc.length > 2 ? (
        <nav className="wiki-toc" aria-label={t("web.wiki.toc")}>
          <strong className="small">{t("web.wiki.toc")}</strong>
          <ul>
            {toc.map((h) => (
              <li key={h.id} className={`toc-${h.level}`}>
                <button type="button" className="link" onClick={() => document.getElementById(h.id)?.scrollIntoView({ behavior: "smooth" })}>
                  {h.text}
                </button>
              </li>
            ))}
          </ul>
        </nav>
      ) : null}
      <Markdown source={data.body} dropTitle={data.page.title} />
      {mini ? <MiniGraph slug={slug} /> : null}
      {data.backlinks.length ? (
        <section className="stack">
          <h3>{t("web.wiki.backlinks")}</h3>
          <ul className="wiki-links">
            {data.backlinks.map((b) => (
              <li key={b.slug}>
                <Link to={hrefOf(b.slug)}>{b.title}</Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {data.links.length ? (
        <section className="stack">
          <h3>{t("web.wiki.links")}</h3>
          <ul className="wiki-links">
            {data.links.map((b) => (
              <li key={b.slug}>
                <Link to={hrefOf(b.slug)}>{b.title}</Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </article>
  );
}

type WikiCount = { name: string; count: number };

function Filters({ q, source, tag, sources, tags, set }: {
  q: string;
  source: string;
  tag: string;
  sources: WikiCount[];
  tags: WikiCount[];
  set: (patch: Record<string, string>) => void;
}): ReactNode {
  const t = useT();
  return (
    <>
      <input type="search" value={q} placeholder={t("web.wiki.search")} aria-label={t("web.wiki.search")} onChange={(e) => set({ q: e.target.value })} />
      {sources.length > 1 ? (
        <select aria-label={t("web.wiki.source")} value={source} onChange={(e) => set({ source: e.target.value })}>
          <option value="">{t("web.wiki.source_all")}</option>
          {sources.map((s) => (
            <option key={s.name} value={s.name}>
              {`${s.name} (${s.count})`}
            </option>
          ))}
        </select>
      ) : null}
      {tags.length ? (
        <div className="row">
          {tags.slice(0, TOP_TAGS).map((c) => (
            <button key={c.name} type="button" className={c.name === tag ? "chip chip-button chip-running" : "chip chip-button"} aria-pressed={c.name === tag} onClick={() => set({ tag: c.name === tag ? "" : c.name })}>
              {c.name}
            </button>
          ))}
        </div>
      ) : null}
    </>
  );
}

export function WikiPage() {
  const t = useT();
  const slug = useParams()["*"] ?? "";
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const tag = params.get("tag") ?? "";
  const source = params.get("source") ?? "";
  const graphView = params.get("view") === "graph";
  const scope = ["1", "2"].includes(params.get("scope") ?? "") ? (params.get("scope") as string) : "all";
  const layerList = (params.get("layers") ?? "").split(",");
  const layers = { tags: layerList.includes("tags"), shared: layerList.includes("shared"), models: layerList.includes("models") };
  const list = useWikiList({ q, source, tag }, !graphView);
  const sync = useWikiSync();
  const data = list.data;
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(patch)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    setParams(next, { replace: true });
  };
  const filters = (sources: WikiCount[], tags: WikiCount[]) => <Filters q={q} source={source} tag={tag} sources={sources} tags={tags} set={set} />;
  return (
    <div className="stack">
      <div className="row">
        <h2>{t("web.wiki.title")}</h2>
        <div className="tabs" role="group" aria-label={t("web.wiki.view")}>
          {(["list", "graph"] as const).map((v) => (
            <button key={v} type="button" className={(v === "graph") === graphView ? "tab active" : "tab"} aria-pressed={(v === "graph") === graphView} onClick={() => set({ view: v === "graph" ? "graph" : "" })}>
              {t(`web.wiki.view.${v}`)}
            </button>
          ))}
        </div>
        <span className="spacer" />
        {data?.synced_at ? <span className="muted small">{t("web.wiki.synced_at", { when: data.synced_at.slice(0, 16).replace("T", " ") })}</span> : null}
        <button type="button" disabled={sync.isPending} onClick={() => sync.mutate()}>
          {sync.isPending ? t("web.wiki.syncing") : t("web.wiki.sync")}
        </button>
      </div>
      <ErrorNote error={sync.error} />
      {sync.data ? (
        <div className="card small stack" role="status">
          <span>{t("web.wiki.sync_done", { added: sync.data.added, updated: sync.data.updated, removed: sync.data.removed })}</span>
          {sync.data.warnings.map((w, i) => (
            <span key={i} className="muted">
              {t(`web.wiki.warn.${w.code}`, { source: w.source ?? "", detail: w.detail ?? "" })}
            </span>
          ))}
        </div>
      ) : null}
      {graphView ? (
        <WikiGraphView
          slug={slug}
          q={q}
          source={source}
          tag={tag}
          scope={scope}
          model={params.get("model") ?? ""}
          color={params.get("color") === "tag" ? "tag" : "source"}
          layers={layers}
          filters={filters}
          set={set}
          reader={slug ? <Reader slug={slug} search={`?${params.toString()}`} mini={false} /> : null}
        />
      ) : (
        <div className="split wiki-split">
          <aside className="stack">
            {filters(data?.sources ?? [], data?.tags ?? [])}
            <ErrorNote error={list.error} />
            {data && data.hidden > 0 ? <p className="muted small">{t("web.wiki.hidden", { n: data.hidden })}</p> : null}
            {data && data.pages.length === 0 ? (
              <p className="empty">{data.total === 0 ? t("web.wiki.empty") : t("web.wiki.no_results")}</p>
            ) : null}
            {data && data.total === 0 && !data.configured ? <p className="muted small">{t("web.wiki.not_configured")}</p> : null}
            <ul className="wiki-list">
              {data?.pages.map((p) => (
                <PageRow key={p.slug} page={p} active={p.slug === slug} />
              ))}
            </ul>
          </aside>
          <section>{slug ? <Reader slug={slug} /> : <p className="empty">{t("web.wiki.pick")}</p>}</section>
        </div>
      )}
    </div>
  );
}
