import { useEffect, useRef, useState } from "react";

export type Source = { id: string; url: string; fetched_at: string };
export type Claim = {
  id: string; label: string; value: string; state: string; vc: string | null; basis: string | null;
  reason: string | null; quote: string | null; source: Source | null; corrected: boolean;
  quote_found: boolean | null; support: string | null; checker: string | null;
  inferred_from?: InferredFrom;
};
export type BasisFact = {
  id: string; label: string; value: string; state: string; source: Source | null; quote: string | null;
  reason: string | null; quote_found: boolean | null; support: string | null;
};
export type InferredFrom = {
  reasoning: string; established: boolean; facts: BasisFact[]; pages: Source[]; changed: string[]; dropped: string[];
};
export type Brief = {
  id: string; version: number; brand_name: string; route: "site" | "description"; facts: Claim[];
  interpretation: Claim[]; unknowns: { label: string; why_it_matters: string }[];
  corrections: { label: string }[]; pages_read: number; read_at: string; unknown_basis: string;
};
export type Row = { key: string; label: string; status: string; detail: string };
export type ActivityEntry = {
  seq: number; at: string; kind: "activity" | "founder"; stage?: string; role?: string; tool?: string;
  task: string; detail: string; status?: string;
};
export type Project = {
  id: string; url: string; site: string; mode: string; demo: boolean;
  intent: { objective: string; ceiling_usd: number | null; include: string | null; exclude: string | null } | null;
  run: { id: string; kind: string; status: string; message: string } | null;
  reading: Row[]; brief: Brief | null; activity: ActivityEntry[];
  usage: { model_calls: number; est_usd: number; pages_fetched: number; pages_read: number; replayed: boolean };
  last_seq: number;
};
export type SourceDetail = { id: string; url: string; title: string; fetched_at: string; excerpt: string; replayed: boolean };

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { ...init, headers: { "content-type": "application/json", ...(init?.headers || {}) } });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `Request failed (${r.status})`);
  return body as T;
}

export const api = {
  create: (body: object, idem: string) =>
    call<{ project_id: string }>("/api/projects", { method: "POST", body: JSON.stringify(body), headers: { "Idempotency-Key": idem } }),
  project: (id: string) => call<Project>(`/api/projects/${id}`),
  correct: (id: string, base_version: number, claim_id: string, value: string) =>
    call<{ version: number; notice: string }>(`/api/projects/${id}/brief`, {
      method: "PATCH", body: JSON.stringify({ base_version, claim_id, value }),
    }),
  retry: (id: string) => call(`/api/projects/${id}/brand-read/retry`, { method: "POST" }),
  describe: (id: string, text: string) =>
    call(`/api/projects/${id}/brand-read/description`, { method: "POST", body: JSON.stringify({ text }) }),
  cancel: (runId: string) => call(`/api/runs/${runId}/cancel`, { method: "POST" }),
  source: (id: string, claim: string) => call<SourceDetail>(`/api/sources/${id}?claim=${claim}`),
};

/** The project view, refreshed whenever the server appends an event (SSE resumes from the last seq). */
export function useProject(id: string) {
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef(false);

  useEffect(() => {
    let alive = true;
    const load = () => {
      if (pending.current) return;
      pending.current = true;
      api.project(id)
        .then((p) => alive && (setProject(p), setError(null)))
        .catch((e) => alive && setError(e.message))
        .finally(() => (pending.current = false));
    };
    load();
    const es = new EventSource(`/api/projects/${id}/events`);
    let timer: number | undefined;
    es.onmessage = () => {
      clearTimeout(timer);
      timer = window.setTimeout(load, 120);
    };
    return () => {
      alive = false;
      es.close();
      clearTimeout(timer);
    };
  }, [id]);

  const reload = () => api.project(id).then(setProject);
  return { project, error, reload };
}

export function shortDate(iso?: string | null) {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

export function pathOf(url: string) {
  try {
    const u = new URL(url);
    return u.hostname.replace(/^www\./, "") + (u.pathname === "/" ? "" : u.pathname.replace(/\/$/, ""));
  } catch {
    return url;
  }
}
