import { useStoryEdit } from "../api/hooks";
import type { ReviewShot } from "../api/review";
import type { Asset } from "../api/types";
import { AssetPreview } from "./Candidates";
import { useT } from "../i18n";

/** Which shot this clip's tail joins.``end_shot_id``: null = automatic (the next shot), 0 = cut free (first frame
 * only), >0 = end on that shot's keyframe. */
export function KeyframeStrip({ pid, shots }: { pid: number; shots: ReviewShot[] }) {
  const t = useT();
  const edit = useStoryEdit(pid, "shots");
  const keyframeOf = (s: ReviewShot): Asset | undefined =>
    (s.assets.find((a) => a.kind === "image.generate") ?? s.assets[0]) as Asset | undefined;
  const setEnd = (shotId: number, endShotId: number) => {
    edit.update.mutate({ itemId: shotId, body: { end_shot_id: endShotId } });
  };
  // The story-order index: end_shot_id null means "the next one", which we resolve for display.
  const idx = (id: number) => shots.findIndex((s) => s.shot_id === id);
  const nextId = (id: number) => {
    const i = idx(id);
    return i >= 0 && i < shots.length - 1 ? shots[i + 1]!.shot_id : null;
  };
  return (
    <div className="strip">
      {shots.map((s) => {
        const kf = keyframeOf(s);
        const end = s.end_shot_id;
        const effective = end === null || end === undefined ? nextId(s.shot_id) : end === 0 ? null : end;
        return (
          <div key={s.shot_id} className="strip-cell">
            <div className="strip-pic">{kf ? <AssetPreview asset={kf} /> : <span className="muted small">{t("web.review.no_takes")}</span>}</div>
            <div className="strip-label small muted">{shotIdLabel(s)}</div>
            <div className="strip-link">
              <select
                value={end === 0 ? 0 : end === null || end === undefined ? -1 : end}
                onChange={(e) => setEnd(s.shot_id, Number(e.target.value))}
                disabled={edit.update.isPending}
              >
                <option value={-1}>{t("web.review.end_auto", { next: effective ? shotIdLabel(shots.find((x) => x.shot_id === effective) ?? s) : t("web.review.end_none") })}</option>
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
