import { Fragment, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAskWiki } from "../api/wiki";
import type { WikiAnswer } from "../api/types";
import { useI18n } from "../i18n";
import { ErrorNote } from "./ErrorNote";

const MARK = /\[(\d{1,2})\]/g;

/** The answer with its [n] marks turned into links to the page the excerpt came from. */
function AnswerText({ answer, href }: { answer: WikiAnswer; href: (slug: string) => string }) {
  const byNumber = new Map(answer.sources.map((s) => [s.n, s]));
  const parts = answer.answer.split(MARK);
  return (
    <p className="wiki-answer">
      {parts.map((part, i) => {
        if (i % 2 === 0) return <Fragment key={i}>{part}</Fragment>;
        const source = byNumber.get(Number(part));
        return source ? (
          <Link key={i} className="wiki-cite" to={href(source.slug)} title={source.title}>
            {part}
          </Link>
        ) : (
          <Fragment key={i}>{`[${part}]`}</Fragment>
        );
      })}
    </p>
  );
}

/** A question box over the knowledge base: the answer is written from the pages below it, each statement pointing at
 *  its source, and the pages it rests on are lit up in the graph (`onAnswer`). */
export function WikiAsk({ source, onAnswer }: { source: string; onAnswer: (slugs: string[]) => void }) {
  const { t, locale } = useI18n();
  const [params] = useSearchParams();
  const [question, setQuestion] = useState("");
  const ask = useAskWiki();
  const answer = ask.data;
  const href = (slug: string) => {
    const keep = new URLSearchParams(params);
    keep.delete("model");
    const rest = keep.toString();
    return `/wiki/${slug}${rest ? `?${rest}` : ""}`;
  };
  const submit = (ev: FormEvent) => {
    ev.preventDefault();
    const text = question.trim();
    if (text.length < 2 || ask.isPending) return;
    onAnswer([]);
    ask.mutate({ question: text, language: locale, source: source || undefined }, { onSuccess: (a) => onAnswer(a.sources.filter((s) => s.cited).map((s) => s.slug)) });
  };
  const cited = answer?.sources.filter((s) => s.cited) ?? [];
  const others = answer?.sources.filter((s) => !s.cited) ?? [];
  return (
    <section className="card stack wiki-ask" aria-label={t("web.wiki.ask.title")}>
      <form className="row" onSubmit={submit}>
        <input type="text" className="grow" value={question} maxLength={500} placeholder={t("web.wiki.ask.placeholder")} aria-label={t("web.wiki.ask.title")} onChange={(e) => setQuestion(e.target.value)} />
        <button type="submit" disabled={ask.isPending || question.trim().length < 2}>
          {ask.isPending ? t("web.wiki.ask.asking") : t("web.wiki.ask.submit")}
        </button>
        {answer || ask.error ? (
          <button
            type="button"
            onClick={() => {
              ask.reset();
              onAnswer([]);
            }}
          >
            {t("web.wiki.ask.clear")}
          </button>
        ) : null}
      </form>
      <ErrorNote error={ask.error} />
      {answer?.reason === "no_match" ? <p className="muted">{t("web.wiki.ask.none")}</p> : null}
      {answer && answer.reason !== "no_match" ? (
        <div className="stack" role="status">
          {answer.found ? null : <p className="muted">{t("web.wiki.ask.not_found")}</p>}
          <AnswerText answer={answer} href={href} />
          {answer.found && !answer.grounded ? <p className="muted small">{t("web.wiki.ask.ungrounded")}</p> : null}
          {cited.length > 0 ? (
            <div className="stack">
              <h3>{t("web.wiki.ask.sources")}</h3>
              <ol className="wiki-sources">
                {cited.map((s) => (
                  <li key={s.n}>
                    <span className="chip">{s.n}</span> <Link to={href(s.slug)}>{s.title}</Link>
                    {s.heading && s.heading !== s.title ? <span className="muted small"> / {s.heading}</span> : null}
                    <span className="chip">{s.source}</span>
                    <span className="muted small wiki-snippet">{s.snippet}</span>
                  </li>
                ))}
              </ol>
            </div>
          ) : null}
          {others.length > 0 ? (
            <details>
              <summary className="small muted">{t("web.wiki.ask.also", { n: others.length })}</summary>
              <ul className="wiki-sources">
                {others.map((s) => (
                  <li key={s.n}>
                    <Link to={href(s.slug)}>{s.title}</Link>
                    {s.heading && s.heading !== s.title ? <span className="muted small"> / {s.heading}</span> : null}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
          <p className="muted small">{t("web.wiki.ask.by", { model: answer.model, seconds: answer.seconds })}</p>
        </div>
      ) : null}
    </section>
  );
}
