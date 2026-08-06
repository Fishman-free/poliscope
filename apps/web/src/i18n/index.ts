/** Minimal three-language UI switching (round 4), zero dependencies.
 *
 * Design: the source strings in the UI are Simplified Chinese, and the
 * zh-Hans dictionary is the identity -- `t()` returns the key unchanged for
 * Chinese. The zh-Hant and en dictionaries map the same source strings to
 * their translations; an untranslated string falls back to the source (the
 * UI degrades to Chinese rather than to an empty string).
 *
 * Reactivity: `useLocale()` subscribes through `useSyncExternalStore`, so
 * every component calling `t()` re-renders when the user switches language.
 * The choice is persisted in localStorage and defaults to the browser
 * language (zh* → zh-Hans, zh-HK/zh-TW/zh-MO → zh-Hant, anything else → en).
 */

import { useSyncExternalStore } from "react";

export type Locale = "zh-Hans" | "zh-Hant" | "en";

export const LOCALES: Locale[] = ["zh-Hans", "zh-Hant", "en"];

const STORAGE_KEY = "poliscope_locale";

let locale: Locale = detectInitialLocale();
const listeners = new Set<() => void>();

function detectInitialLocale(): Locale {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "zh-Hans" || saved === "zh-Hant" || saved === "en") {
      return saved;
    }
  } catch {
    // localStorage unavailable (private mode): fall through to detection.
  }
  const language = (navigator.language || "").toLowerCase();
  if (language.startsWith("zh")) {
    if (["zh-hk", "zh-tw", "zh-mo", "zh-mo"].includes(language)) {
      return "zh-Hant";
    }
    return "zh-Hans";
  }
  return "en";
}

export function getLocale(): Locale {
  return locale;
}

export function setLocale(next: Locale): void {
  locale = next;
  try {
    window.localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // Persistence is best-effort; the in-memory choice still applies.
  }
  for (const listener of listeners) {
    listener();
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useLocale(): Locale {
  return useSyncExternalStore(subscribe, getLocale);
}

/** Translate a Simplified-Chinese source string into the current locale.
 * ``{0}``/``{1}`` placeholders are substituted with ``args`` after lookup, so
 * translated templates keep the same slots as the source. Each locale looks
 * up its own dictionary first -- an English UI must never fall back to the
 * traditional-Chinese entry -- then to English, then to the source. */
export function t(text: string, ...args: (string | number)[]): string {
  let translated: string;
  if (locale === "zh-Hans") {
    translated = text;
  } else if (locale === "zh-Hant") {
    translated = zhHant[text] ?? en[text] ?? text;
  } else {
    translated = en[text] ?? text;
  }
  if (args.length === 0) {
    return translated;
  }
  return translated.replace(/\{(\d+)\}/g, (_match, index: string) => {
    const value = args[Number(index)];
    return value === undefined ? _match : String(value);
  });
}

import { zhHant } from "./zh-Hant";
import { en } from "./en";

/** Human-readable label for the locale switcher. */
export const LOCALE_LABELS: Record<Locale, string> = {
  "zh-Hans": "简体中文",
  "zh-Hant": "繁體中文",
  en: "English",
};
