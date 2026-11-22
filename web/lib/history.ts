import type { PublicResult } from "@/lib/api";

/** "My checks": on this device only. The server forgets results after 24 hours. */
export interface SavedCheck {
  id: string;
  at: number;
  result: PublicResult;
}

const KEY = "truefalse.history";
const MAX = 30;

export function savedChecks(): SavedCheck[] {
  try {
    const list: unknown = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(list) ? list.filter((c) => c?.id && c?.result) : [];
  } catch {
    return [];
  }
}

function write(list: SavedCheck[]) {
  try {
    localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    /* private window or full storage: history is a convenience */
  }
}

export function saveCheck(id: string, result: PublicResult) {
  const list = savedChecks();
  if (list.some((c) => c.id === id)) return;
  write([{ id, at: Date.now(), result }, ...list].slice(0, MAX));
}

export function savedCheck(id: string): SavedCheck | undefined {
  return savedChecks().find((c) => c.id === id);
}

export function forgetCheck(id: string): SavedCheck[] {
  const list = savedChecks().filter((c) => c.id !== id);
  write(list);
  return list;
}

export function forgetAll() {
  write([]);
}
