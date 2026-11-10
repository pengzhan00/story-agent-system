import { useStoryEdit } from "../api/hooks";
import type { ReviewShot } from "../api/review";
import type { Asset } from "../api/types";
import { AssetPreview } from "./Candidates";
import { useT } from "../i18n";

/** Which shot this clip's tail joins. ``end_shot_id``: null = automatic (first frame only unless the server's
 * ``[workflow] video_chain_end`` is on), 0 = cut free (first frame only), >0 = end on that shot's keyframe. */
export function KeyframeStrip({ pid, shots }: { pid: number; shots: ReviewShot[] }) {
  const t = useT();
  const edit = useStoryEdit(pid, "shots");
  const keyframeOf = (s: ReviewShot): Asset | undefined =>
    (s.assets.find((a) => a.kind === "image.generate") ?? s.assets[0]) as Asset | undefined;
  const AUTO = -1; // the select's value for "automatic"; the API takes null for it
  const setEnd = (shotId: number, choice: number) => {
    edit.update.mutate({ itemId: shotId, body: { end_shot_id: choice === AUTO ? null : choice } });
  };
  return (
    <div className="strip">
      {shots.map((s) => {
        const kf = keyframeOf(s);
        const end = s.end_shot_id;
        return (
          <div key={s.shot_id} className="strip-cell">
            <div className="strip-pic">{kf ? <AssetPreview asset={kf} /> : <span className="muted small">{t("web.review.no_takes")}</span>}</div>
            <div className="strip-label small muted">{shotIdLabel(s)}</div>
            <div className="strip-link">
              <select
                value={end === 0 ? 0 : end === null || end === undefined ? AUTO : end}
                onChange={(e) => setEnd(s.shot_id, Number(e.target.value))}
                disabled={edit.update.isPending}
              >
                <option value={AUTO}>{t("web.review.end_auto")}</option>
                <option value={0}>{t("web.review.end_free")}</option>
                {shots.filter((x) => x.shot_id !== s.shot_id).map((x) => (
                  <option key={x.shot_id} value={x.shot_id}>
                    {t("web.review.end_to", { target: shotIdLabel(x) })}
                  </option>
                ))}
              </select>
            </div>
            {edit.update.isPending ? <span className="small muted">{t("web.loading")}</span> : null}
          </div>
        );
      })}
    </div>
  );
}

function shotIdLabel(s: ReviewShot): string {
  return `${s.act}.${s.scene}.${s.shot}`;
}
