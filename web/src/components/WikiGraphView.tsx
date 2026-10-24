import { useMemo, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useWikiGraph } from "../api/wiki";
import type { WikiGraphNode } from "../api/types";
import { useT } from "../i18n";
import { paletteOf } from "../lib/graph";
import { ErrorNote } from "./ErrorNote";
import { GRAPH_COLORS, WikiGraph, colorOf } from "./WikiGraph";

export const GRAPH_LIMIT = 300;
export const GRAPH_LIMIT_MAX = 1000;
const HEIGHT = 520;
const HEIGHT_BESIDE_READER = 640; // the picture is narrower when a page is open next to it, so it is drawn taller
const MINI_HEIGHT = 220;
const MODEL_PREFIX = "model:";

const pageHref = (slug: string, search = "") => `/wiki/${slug}${search}`;

/** What clicking a node does: a page opens, a tag filters by itself, a model shows the notes that mention it. */
function useActivate(set: (patch: Record<string, string>) => void, tag: string) {
  const navigate = useNavigate();
  const { search } = useLocation();
  return (node: WikiGraphNode) => {
    if (node.kind === "page") navigate(pageHref(node.slug, search));
    else if (node.kind === "tag") set({ tag: node.label === tag ? "" : node.label });
    else set({ model: node.id.slice(MODEL_PREFIX.length) });
  };
}

interface ViewProps {
  slug: string;
  q: string;
  source: string;
  tag: string;
  scope: string;
  model: string;
  color: "source" | "tag";
  layers: { tags: boolean; shared: boolean; models: boolean };
  /** the search / source / tag controls, drawn by the page so both views share them */
  filters: (sources: { name: string; count: number }[], tags: { name: string; count: number }[]) => ReactNode;
  set: (patch: Record<string, string>) => void;
  reader: ReactNode;
}

export function WikiGraphView({ slug, q, source, tag, scope, model, color, layers, filters, set, reader }: ViewProps) {
  const t = useT();
  const [limit, setLimit] = useState(GRAPH_LIMIT);
  const depth = scope === "2" ? 2 : 1;
  const focus = model ? `${MODEL_PREFIX}${model}` : scope !== "all" && slug ? slug : "";
  const graph = useWikiGraph({ q, source, tag, focus, depth, tags: layers.tags, shared: layers.shared, models: layers.models, limit });
  const data = graph.data;
  const nodes = useMemo(() => data?.nodes ?? [], [data]);
  const edges = useMemo(() => data?.edges ?? [], [data]);
  const palette = useMemo(() => paletteOf(nodes, color), [nodes, color]);
  const activate = useActivate(set, tag);
  const { search } = useLocation();
  const isolated = nodes.filter((n) => n.isolated);
  const linkCount = edges.filter((e) => e.kind === "link").length;
  const pageCount = nodes.filter((n) => n.kind === "page").length;
  const kinds = new Set(edges.map((e) => e.kind));
  const modelNode = model ? nodes.find((n) => n.id === `${MODEL_PREFIX}${model}`) : undefined;
  const canvasNodes = nodes.filter((n) => !n.isolated);

  return (
    <div className="stack">
      <div className="stack wiki-graph-controls">
        {filters(data?.sources ?? [], data?.tags ?? [])}
        <div className="row">
          <label className="field">
            <span>{t("web.wiki.graph.scope")}</span>
            <select value={scope} onChange={(e) => set({ scope: e.target.value })}>
              <option value="all">{t("web.wiki.graph.scope_all")}</option>
              <option value="1">{t("web.wiki.graph.scope_1")}</option>
              <option value="2">{t("web.wiki.graph.scope_2")}</option>
            </select>
          </label>
          <label className="field">
            <span>{t("web.wiki.graph.color")}</span>
            <select value={color} onChange={(e) => set({ color: e.target.value })}>
              <option value="source">{t("web.wiki.graph.color_source")}</option>
              <option value="tag">{t("web.wiki.graph.color_tag")}</option>
            </select>
          </label>
          <fieldset className="row wiki-layers">
            <legend className="small muted">{t("web.wiki.graph.layers")}</legend>
            {(["tags", "shared", "models"] as const).map((layer) => (
              <label key={layer} className="check">
                <input
                  type="checkbox"
                  checked={layers[layer]}
                  onChange={(e) => {
                    const next = { ...layers, [layer]: e.target.checked };
                    set({ layers: (Object.keys(next) as (keyof typeof next)[]).filter((k) => next[k]).join(",") });
                  }}
                />
                <span>{t(`web.wiki.graph.layer_${layer}`)}</span>
              </label>
            ))}
          </fieldset>
        </div>
        {scope !== "all" && !slug && !model ? <p className="muted small">{t("web.wiki.graph.scope_need_page")}</p> : null}
      </div>
      <ErrorNote error={graph.error} />
      {model ? (
        <div className="card stack">
          <div className="row">
            <h3>{t("web.wiki.graph.model_title", { model })}</h3>
            <span className="spacer" />
            <button type="button" onClick={() => set({ model: "" })}>
              {t("web.wiki.graph.model_clear")}
            </button>
          </div>
          {modelNode ? <p className="small muted">{t("web.wiki.graph.model_refs", { refs: modelNode.refs.join(", ") })}</p> : null}
          <p className="small muted">
            {t("web.wiki.graph.model_hint")} <Link to="/projects">{t("web.wiki.graph.model_projects")}</Link>
          </p>
        </div>
      ) : null}
      <div className={slug ? "wiki-graph-split" : "stack"}>
        <div className="stack">
          {data && canvasNodes.length === 0 ? <p className="empty">{t("web.wiki.graph.empty")}</p> : null}
          {canvasNodes.length > 0 ? (
            <WikiGraph nodes={nodes} edges={edges} palette={palette} height={slug ? HEIGHT_BESIDE_READER : HEIGHT} current={slug || undefined} fadeContext={q.trim() !== ""} onActivate={activate} />
          ) : null}
          {data ? (
            <div className="row small muted">
              <span>{t("web.wiki.graph.stats", { pages: pageCount, links: linkCount })}</span>
              {data.hidden > 0 ? <span>{t("web.wiki.hidden", { n: data.hidden })}</span> : null}
            </div>
          ) : null}
          {data?.truncated ? (
            <div className="card small row" role="status">
              <span>{t("web.wiki.graph.truncated", { shown: pageCount, matched: data.matched })}</span>
              {limit < GRAPH_LIMIT_MAX ? (
                <button type="button" onClick={() => setLimit(GRAPH_LIMIT_MAX)}>
                  {t("web.wiki.graph.more", { n: GRAPH_LIMIT_MAX })}
                </button>
              ) : null}
            </div>
          ) : null}
          {q.trim() !== "" ? <p className="small muted">{t("web.wiki.graph.fade_hint")}</p> : null}
          {canvasNodes.length > 0 ? <Legend palette={palette} kinds={kinds} nodeKinds={new Set(nodes.map((n) => n.kind))} /> : null}
          {canvasNodes.length > 0 ? <p className="small muted">{t("web.wiki.graph.hint")}</p> : null}
          {isolated.length > 0 ? (
            <section className="stack">
              <h3>{t("web.wiki.graph.isolated", { n: isolated.length })}</h3>
              <p className="small muted">{t("web.wiki.graph.isolated_hint")}</p>
              <ul className="row wiki-isolated">
                {isolated.map((n) => (
                  <li key={n.id}>
                    <Link className="chip chip-button" to={pageHref(n.slug, search)}>
                      {n.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </div>
        {slug ? <div className="wiki-graph-reader">{reader}</div> : null}
      </div>
    </div>
  );
}

function Legend({ palette, kinds, nodeKinds }: { palette: ReturnType<typeof paletteOf>; kinds: Set<string>; nodeKinds: Set<string> }) {
  const t = useT();
  return (
    <ul className="row small wiki-legend" aria-label={t("web.wiki.graph.legend")}>
      {palette.legend.map((name, i) => (
        <li key={name} className="row">
          <span className="swatch" style={{ background: GRAPH_COLORS[i] }} />
          <span>{name}</span>
        </li>
      ))}
      {palette.by === "tag" ? (
        <li className="row">
          <span className="swatch" style={{ background: colorOf(palette, "") }} />
          <span>{t("web.wiki.graph.legend.other")}</span>
        </li>
      ) : null}
      {nodeKinds.has("tag") ? (
        <li className="row">
          <span className="swatch square" />
          <span>{t("web.wiki.graph.legend.tag_node")}</span>
        </li>
      ) : null}
      {nodeKinds.has("model") ? (
        <li className="row">
          <span className="swatch diamond" />
          <span>{t("web.wiki.graph.legend.model_node")}</span>
        </li>
      ) : null}
      {(["link", "tag", "shared_tag", "mention"] as const)
        .filter((k) => kinds.has(k))
        .map((k) => (
          <li key={k} className="row">
            <svg width="24" height="8" aria-hidden="true">
              <line x1="0" y1="4" x2="24" y2="4" className={`g-edge g-edge-${k}`} />
            </svg>
            <span>{t(`web.wiki.graph.legend.${k}`)}</span>
          </li>
        ))}
    </ul>
  );
}

/** The few pages around one page (and the models its text names), small enough to sit in the page reader. */
export function MiniGraph({ slug }: { slug: string }) {
  const t = useT();
  const navigate = useNavigate();
  const { data } = useWikiGraph({ q: "", source: "", tag: "", focus: slug, depth: 1, tags: false, shared: false, models: true, limit: 40 });
  const palette = useMemo(() => paletteOf(data?.nodes ?? [], "source"), [data]);
  if (!data || data.nodes.filter((n) => !n.isolated).length < 2) return null;
  return (
    <section className="stack" aria-label={t("web.wiki.graph.mini_title")}>
      <div className="row">
        <h3>{t("web.wiki.graph.mini_title")}</h3>
        <span className="spacer" />
        <Link className="small" to={`${pageHref(slug)}?view=graph&scope=1`}>
          {t("web.wiki.graph.mini_open")}
        </Link>
      </div>
      <WikiGraph
        compact
        nodes={data.nodes}
        edges={data.edges}
        palette={palette}
        height={MINI_HEIGHT}
        current={slug}
        onActivate={(n) => navigate(n.kind === "page" ? pageHref(n.slug) : `/wiki?view=graph&model=${encodeURIComponent(n.id.slice(MODEL_PREFIX.length))}`)}
      />
    </section>
  );
}
