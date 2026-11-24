/** The language the person picked in the header (or their browser's, until they pick one). */

export const LANGS: { code: string; name: string }[] = [
  { code: "en", name: "English" },
  { code: "es", name: "Español" },
  { code: "pt", name: "Português" },
  { code: "fr", name: "Français" },
  { code: "de", name: "Deutsch" },
  { code: "hi", name: "हिन्दी" },
  { code: "ar", name: "العربية" },
  { code: "zh", name: "中文" },
  { code: "id", name: "Bahasa Indonesia" },
  { code: "sw", name: "Kiswahili" },
  { code: "bn", name: "বাংলা" },
  { code: "ta", name: "தமிழ்" },
  { code: "ur", name: "اردو" },
];

const KEY = "truefalse.lang";
export const LANG_EVENT = "truefalse-lang";

/** Picked language, or "" when the person hasn't picked one (answers follow the message). */
export function pickedLanguage(): string {
  try {
    return localStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

export function uiLanguage(): string {
  return pickedLanguage() || (typeof navigator !== "undefined" ? navigator.language : "") || "en";
}

export function pickLanguage(code: string): void {
  try {
    if (code) localStorage.setItem(KEY, code);
    else localStorage.removeItem(KEY);
  } catch {
    /* not remembered, still applied for this page */
  }
  window.dispatchEvent(new Event(LANG_EVENT));
}
