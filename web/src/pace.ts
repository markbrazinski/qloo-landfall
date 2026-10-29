import { useEffect, useState } from "react";

// Owner, 28 Oct 2026: work done ahead of the founder still plays its animation for a minimum time.
const seen = new Map<string, number>();  // the fallback when session storage is blocked

/** 0 → 1 over `ms` from the first time this page showed `key` in this session; 1 at once when there is no key, or when
 * the first sight wasn't `eligible` (e.g. a finished build revisited later). Re-renders while it plays. */
export function useMinPlay(key: string | null | undefined, ms: number, eligible = true): number {
  const [, tick] = useState(0);
  let start = 0;
  if (key) {
    const k = `lf-play-${key}`;
    if (!seen.has(k)) {
      let s: number | null = null;
      try { const v = sessionStorage.getItem(k); s = v == null ? null : Number(v); } catch { /* blocked: memory only */ }
      if (s == null) { s = eligible ? Date.now() : 0; try { sessionStorage.setItem(k, String(s)); } catch { /* blocked */ } }
      seen.set(k, s);
    }
    start = seen.get(k)!;
  }
  const t = start ? Math.min(1, (Date.now() - start) / ms) : 1;
  const playing = t < 1;
  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => tick((n) => n + 1), 200);
    return () => clearInterval(id);
  }, [playing]);
  return t;
}

/** True the first time `key` is asked for in this session, then false: for a step that happens by itself only once. */
export function once(key: string): boolean {
  const k = `lf-once-${key}`;
  let done = seen.has(k);
  try { done ||= sessionStorage.getItem(k) != null; sessionStorage.setItem(k, "1"); } catch { /* blocked: memory only */ }
  seen.set(k, 1);
  return !done;
}
