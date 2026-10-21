import { useEffect } from "react";
import { Link } from "react-router-dom";
import { useLaunch, useStartAll, useStartService } from "../api/hooks";
import { ErrorNote } from "../components/ErrorNote";
import { ProgressBar } from "../components/ProgressBar";
import { useT } from "../i18n";

export const LAUNCH_SEEN = "storyagent.launch.seen";

const TONE: Record<string, string> = {
  running: "completed",
  probe_only: "stale",
  starting: "running",
  failed: "failed",
  stopped: "queued",
  disabled: "stale",
};

/** First screen of a browser session: what is running, what is missing, and a button that starts what is needed. */
export function LaunchPage() {
  const t = useT();
  const launch = useLaunch();
  const startAll = useStartAll();
  const startOne = useStartService();
  useEffect(() => {
    try {
      sessionStorage.setItem(LAUNCH_SEEN, "1");
    } catch {
      /* the redirect then simply repeats on the next visit */
    }
  }, []);
  const data = launch.data;
  const busy = startAll.isPending || startOne.isPending;
  return (
    <div className="stack">
      <h1>{t("web.launch.title")}</h1>
      <p className="muted">{t("web.launch.subtitle")}</p>
      <ErrorNote error={launch.error ?? startAll.error ?? startOne.error} />
      {launch.isLoading ? <p className="muted">{t("web.loading")}</p> : null}
      {data ? (
        <>
          <div className={`banner ${data.ready ? "banner-ok" : "banner-warn"}`} role="status">
            <strong>{data.ready ? t("web.launch.ready") : t("web.launch.not_ready")}</strong>
            {data.starting ? <span className="muted"> {t("web.launch.starting")}</span> : null}
          </div>
          <div className="row">
            <button className="primary" onClick={() => startAll.mutate()} disabled={busy || data.startable.length === 0}>
              {t("web.launch.start_all")}
            </button>
            <button onClick={() => launch.refetch()} disabled={launch.isFetching}>
              {t("web.launch.recheck")}
            </button>
            <span className="spacer" />
            <Link className="button" to="/projects">
              {t("web.launch.enter")}
            </Link>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("web.launch.col.service")}</th>
                  <th>{t("web.launch.col.state")}</th>
                  <th>{t("web.launch.col.action")}</th>
                </tr>
              </thead>
              <tbody>
                {data.services.map((s) => (
                  <tr key={s.name}>
                    <td>
                      <span className="strong">{s.name}</span>{" "}
                      {s.required ? <span className="chip chip-queued">{t("web.launch.required")}</span> : null}
                      <div className="muted small">{s.url}</div>
                      {s.hint ? <div className="muted small">{s.hint}</div> : null}
                    </td>
                    <td>
                      <span className={`chip chip-${TONE[s.state] ?? "stale"}`}>{s.label}</span>
                      {s.state === "starting" && s.timeout_sec ? (
                        <>
                          <ProgressBar
                            percent={Math.min(((s.elapsed_sec ?? 0) / s.timeout_sec) * 100, 99)}
                            label={s.label}
                          />
                          <div className="muted small">
                            {t("web.launch.elapsed", { sec: Math.round(s.elapsed_sec ?? 0), max: Math.round(s.timeout_sec) })}
                          </div>
                        </>
                      ) : null}
                      {s.state === "failed" && s.log ? (
                        <div className="muted small">{t("web.launch.log", { path: s.log })}</div>
                      ) : null}
                      {s.state !== "running" && s.detail ? <div className="muted small">{s.detail}</div> : null}
                    </td>
                    <td>
                      {s.startable ? (
                        s.state === "stopped" || s.state === "failed" ? (
                          <button onClick={() => startOne.mutate(s.name)} disabled={busy}>
                            {t("web.launch.start")}
                          </button>
                        ) : null
                      ) : s.state === "stopped" ? (
                        <span className="muted small">{t("web.launch.manual")}</span>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h2>{t("web.launch.issues")}</h2>
          {data.issues.length === 0 ? (
            <p className="muted">{t("web.launch.no_issues")}</p>
          ) : (
            <ul className="issue-list">
              {data.issues.map((i) => (
                <li key={i.name}>
                  <span className={`chip chip-${i.required ? "failed" : "stale"}`}>{i.label}</span>{" "}
                  <span className="strong">{i.name}</span> <span className="muted small">{i.detail}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : null}
    </div>
  );
}
