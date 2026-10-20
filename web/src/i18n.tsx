import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { setApiLocale } from "./api/client";

export type Params = Record<string, string | number>;
export type T = (key: string, params?: Params) => string;

const KEY = "storyagent.locale";
const FALLBACK = "en";

export function interpolate(template: string, params?: Params): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (m, name: string) => (name in params ? String(params[name]) : m));
}

export function makeT(catalog: Record<string, string>): T {
  return (key, params) => interpolate(catalog[key] ?? key, params);
}

function initialLocale(): string {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved) return saved;
  } catch {
    /* storage unavailable */
  }
  return navigator.language.toLowerCase().startsWith("zh") ? "zh-CN" : FALLBACK;
}

interface I18n {
  t: T;
  locale: string;
  locales: string[];
  setLocale: (l: string) => void;
}

const Ctx = createContext<I18n | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState(initialLocale);
  const [catalog, setCatalog] = useState<Record<string, string> | null>(null);
  const [locales, setLocales] = useState<string[]>([locale]);

  useEffect(() => {
    fetch("/api/meta")
      .then((r) => (r.ok ? r.json() : null))
      .then((m: { locales?: string[] } | null) => m?.locales && setLocales(m.locales))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    let live = true;
    setApiLocale(locale);
    fetch(`/api/i18n/${encodeURIComponent(locale)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((c: Record<string, string>) => live && setCatalog(c))
      .catch(() => live && locale !== FALLBACK && setLocaleState(FALLBACK));
    return () => {
      live = false;
    };
  }, [locale]);

  const setLocale = useCallback((l: string) => {
    try {
      localStorage.setItem(KEY, l);
    } catch {
      /* ignore */
    }
    setLocaleState(l);
  }, []);

  const value = useMemo<I18n | null>(
    () => (catalog ? { t: makeT(catalog), locale, locales, setLocale } : null),
    [catalog, locale, locales, setLocale],
  );
  if (!value) return null;
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useI18n(): I18n {
  const v = useContext(Ctx);
  if (!v) throw new Error("I18nProvider missing");
  return v;
}
export const useT = (): T => useI18n().t;
