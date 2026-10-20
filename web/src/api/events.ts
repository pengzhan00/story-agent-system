import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { Job, WorkflowOverview, WorkflowRun } from "./types";

export type FeedState = "connecting" | "live" | "offline";

/**
 * One SSE connection for the whole app. `workflow` frames update the run in the cache the moment they arrive
 * (so the bar moves without a request); when a run changes status the lists that depend on it are refetched.
 * If the stream drops the polling in `hooks.ts` keeps the screen moving and EventSource reconnects by itself.
 */
export function useEventFeed(): FeedState {
  const qc = useQueryClient();
  const [state, setState] = useState<FeedState>("connecting");

  useEffect(() => {
    if (typeof EventSource === "undefined") {
      setState("offline");
      return;
    }
    const es = new EventSource("/api/events");
    es.onopen = () => setState("live");
    es.onerror = () => setState(es.readyState === EventSource.CLOSED ? "offline" : "connecting");

    es.addEventListener("workflow", (ev) => {
      const run = JSON.parse((ev as MessageEvent<string>).data) as WorkflowRun;
      const key = ["workflow", run.project_id];
      const before = qc.getQueryData<WorkflowOverview>(key);
      const active = run.status === "queued" || run.status === "running";
      if (before) {
        const prevStatus = before.active_run?.id === run.id ? before.active_run.status : undefined;
        qc.setQueryData<WorkflowOverview>(key, {
          ...before,
          active_run: active ? run : null,
          recent_runs: [run, ...before.recent_runs.filter((r) => r.id !== run.id)].slice(0, 6),
        });
        if (prevStatus !== run.status) {
          qc.invalidateQueries({ queryKey: key });
          qc.invalidateQueries({ queryKey: ["story", run.project_id] });
        }
      } else {
        qc.invalidateQueries({ queryKey: key });
      }
    });

    es.addEventListener("job", (ev) => {
      const job = JSON.parse((ev as MessageEvent<string>).data) as Job;
      qc.setQueriesData<Job[]>({ queryKey: ["jobs"] }, (old) =>
        old ? old.map((j) => (j.id === job.id ? job : j)) : old,
      );
      if (job.status !== "running") {
        qc.invalidateQueries({ queryKey: ["jobs"] });
        qc.invalidateQueries({ queryKey: ["queue"] });
      }
    });
    return () => es.close();
  }, [qc]);

  return state;
}
