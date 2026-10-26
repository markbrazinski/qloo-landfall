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
export type QlooTag = { qloo_id: string; name: string; type: string; type_label?: string };
export type Candidate = {
  qloo_id: string; name: string; types: string[]; popularity: number | null; short_description: string | null;
  external: Record<string, { id?: string }> | null; official_site: string | null; tags: QlooTag[]; tag_count: number;
  corroboration: { name_match: "exact" | "normalised" | "different"; domain_match: boolean | null; site_mentions_name: boolean | null };
};
export type Identity = {
  status: "searching" | "needs_founder" | "decided" | "failed" | "not_started"; settled: boolean;
  id: string | null; version: number | null; site_name: string | null; name_source: string | null; queries: string[];
  candidates: Candidate[]; chosen: Candidate | null; decision: { entity_id: string } | "none" | null;
  decided_by: "landfall" | "founder" | null; basis: string[]; failure_reason: string | null; attempts: number | null; history: number;
};
export type Signal = {
  kind: "tag" | "entity"; qloo_id: string; name: string; tag_type: string | null; type_label: string | null;
  origin: "QLOO" | "QLOO_CATEGORY" | "COMPARABLE" | "YOU_SAID"; included: boolean; reasoning: string | null;
  inferred_from?: InferredFrom;
};
export type Profile = {
  id: string | null; version: number; route: "brand_entity" | "proposed_profile" | "description_profile" | null;
  confirmed: boolean; confirmed_by: string | null; outdated: boolean; signals: Signal[];
  display_tags: { type: string; tags: QlooTag[] }[]; dropped: { qloo_id: string; name: string | null; reason: string }[];
  fact_changed: string[]; run: { id: string; status: string; message: string } | null; proposing: boolean;
  state: "ready" | "proposing" | "failed" | "waiting";
};
export type Scope = "international" | "domestic" | "both";
export type Purpose = "presence" | "stockists" | "test_demand";
export type PresenceSource = { kind: "QLOO_STORE" | "YOUR_SITE" | "HOME" | "YOU_SAID" | "QLOO_KEY_MARKET" | "HOME_COUNTRY"; ref: string | null; name?: string; located?: boolean };
export type Presence = {
  id: string | null; version: number;
  cities: { city_id: string; name: string; country: string; sources: PresenceSource[]; excluded: boolean }[];
  removed_cities: string[];
  countries: { iso: string; name: string; sources: PresenceSource[]; leave_out: boolean }[];
  unresolved_regions: string[]; site_not_used: { claim_id: string; label: string; value: string; state: string }[];
  store_search: { status: string; pages: number; rows: number; rows_matched?: number };
  located_checks: { city_id: string; status: string }[];
  home: { country: string | null; city: string | null; note: string; provenance: string };
  coverage_line: string; leave_out_presence: boolean;
};
export type StandInBrand = { qloo_id: string; name: string; rank_in_request: number | null; origin: "QLOO" | "YOU_SAID" };
export type StandIns = {
  state: "ok" | "none" | "failed" | "pending" | "not_started"; id?: string; version?: number; brands: StandInBrand[];
  excluded?: { qloo_id: string; name: string }[]; genre?: string | null; genre_line?: string; line?: string;
} | null;
export type Audience = {
  state: "ready" | "pending" | "no_signal"; id?: string; version?: number; kind?: "brand" | "standins"; label?: string;
  status?: { audiences: string; demographics: string };
  audiences: { uuid: string; name: string; type: string | null }[]; removed: { uuid: string; name: string }[];
  demographics?: { lines: { signal_id: string; signal_name: string; text: string }[]; summary: string };
  from_previous_standins?: boolean;
};
export type Goal = {
  presence: Presence; standins: StandIns; audience: Audience; needs_home: boolean; home_unknown_international: boolean;
  preparing: boolean; countries: { iso: string; name: string }[]; catalog: { city_id: string; name: string }[];
};
export type Intent = {
  version: number; objective: string; ceiling_usd: number | null; include: string | null; exclude: string | null;
  scope: Scope; purpose: Purpose; leave_out_presence: boolean; home_country: string | null;
};
export type Project = {
  id: string; url: string; site: string; mode: string; demo: boolean;
  intent: Intent | null; goal: Goal | null;
  run: { id: string; kind: string; status: string; message: string } | null;
  reading: Row[]; brief: Brief | null; activity: ActivityEntry[];
  identity: Identity; profile: Profile | null; markets_reason: string;
  markets: { stage: "none" | "ready" | "scanning" | "scanned"; reason: string; outdated?: boolean; shortlisted?: number | null };
  usage: {
    model_calls: number; est_usd: number; pages_fetched: number; pages_read: number; replayed: boolean;
    qloo_requests: number; qloo_lookups: number; qloo_sent: number; qloo_live: number; qloo_cached: number;
  };
  last_seq: number;
};
export type Fit = {
  distance_km: number | null; same_country: boolean | null; import_needed: boolean | null;
  inside_ship_range: boolean | null; chip: string;
};
export type Digest = {
  returned_count: number; take: number; channels: Record<string, number>; unknown_category: number;
  top_places: { qloo_id: string; name: string; genres: string[] }[]; evidence_strength: string;
};
export type Pick = { city_id: string; included: boolean; added_by_founder: boolean; featured: boolean };
export type CityRow = {
  city_id: string; name: string; country: string; region: string; lat: number; lon: number;
  status: "queued" | "searching" | "returned" | "empty" | "failed" | "unresolved";
  digest: Digest | null; fit: Fit; fit_label: string; fit_line: string; finding: string | null; reason: string | null;
  attempts: number | null; origin: string | null; proposed: boolean;
  deferred: { reason: string; line: string } | null; pick: Pick | null;
  excluded: "ALREADY_HERE" | "OUTSIDE_GOAL" | "LEFT_OUT_BY_YOU" | "NOT_RANKED" | null; entry_label: string;
};
export type Comparable = { name: string; stores: number | null; capped: boolean; state: "ok" | "failed" };
export type Card35 = {
  city_id: string; group: "all" | "home" | "abroad"; name: string; region: string; country: string;
  role: string; role_label: string; qloo_position: number; final_position: number; moved_by_purpose: boolean;
  position_line: string; moved_line: string | null; why: string; different: string; could_change: string; ways_in: string;
  prose_source: Record<string, "MODEL" | "DETERMINISTIC">; located_check: string | null;
  places: { qloo_id: string; name: string; genres: string[] }[]; comparables: Comparable[]; scan_status: string;
  scan_missing: boolean; entry_label: string; entry_line: string; fit: Fit; featured: boolean; key_unknown: boolean;
};
export type Ranking = {
  id: string; version: number; state: string; text: string; signal_label: string | null; n_candidates: number;
  positions: Record<string, number>; unranked: Record<string, string>; running: boolean; retryable: boolean; current: boolean;
};
export type PlaceRef = { qloo_id: string; name: string; genres: { qloo_id: string; name: string }[]; address?: string | null };
export type QSignal = { kind: string; qloo_id: string; name: string; origin: string };
export type Notice = {
  kind: "changed" | "failed"; action: "retry_scan" | "retry_shortlist" | "update" | "rescan" | "retry_ranking" | "home" | null;
  text: string; rerank?: boolean;
};
export type Markets = {
  catalog: number; can_start: boolean; regions: string[];
  home: { text: string | null; country: string | null; city: string | null; provenance: string; note: string; lat: number | null; lon: number | null };
  generation: { id: string; version: number; status: string; route: string; signals: QSignal[]; current: boolean } | null;
  run?: { id: string; status: string; message: string } | null;
  shortlist_run?: { id: string; status: string; message: string } | null;
  coverage?: { catalog: number; attempted: number; queued: number; searching: number; returned: number; empty: number; failed: number; unresolved: number };
  usage?: { lookups: number; cached: number; replayed: number; sent: number };
  cities: CityRow[]; notices: Notice[];
  goal: { scope: Scope; purpose: Purpose; leave_out_presence: boolean; home_country: string | null };
  presence: Presence; audience: Audience; standins: StandIns;
  ranking?: Ranking | null;
  ranking_pending?: { running: boolean; signal_label: string | null; n_candidates: number; needs_home: boolean };
  assessment?: {
    id: string; version: number; lead: { text: string; source: "MODEL" | "DETERMINISTIC" | "NONE" };
    groups: { key: "all" | "home" | "abroad"; label: string; cap: number; available: number; city_ids: string[]; top: string | null }[];
    cards: Card35[]; faults: { city_id: string; field: string; reason: string }[]; model_error: string | null;
    ranking_state: string; n_candidates: number; signal: { kind?: string; label?: string; names?: string[] };
    why_order: string; audience_names: string[]; goal: { scope: Scope; purpose: Purpose }; coverage: string; still: string;
  };
  shortlist?: { version: number; cities: Pick[]; cap: number; included: number; added: number };
};
export type PlaceDetail = {
  place: { qloo_id: string; name: string; genres: { name: string }[]; address: string | null; website: string | null;
    phone: string | null; distance_m: number | null; tags_kept: { name: string }[] };
  city: { name: string }; class: string;
  request: { signals: QSignal[]; radius_m: number; rank_note: string; origin: string; fetched_at: string };
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
  chooseIdentity: (id: string, base_version: number, entity_id: string) =>
    call<{ version: number }>(`/api/projects/${id}/identity`, { method: "POST", body: JSON.stringify({ base_version, entity_id }) }),
  retryIdentity: (id: string) => call(`/api/projects/${id}/identity/retry`, { method: "POST" }),
  editProfile: (id: string, base_version: number, signals: { qloo_id: string; kind: string; included: boolean }[], confirm: boolean) =>
    call<{ version: number; confirmed: boolean }>(`/api/projects/${id}/profile`, {
      method: "PUT", body: JSON.stringify({ base_version, signals, confirm }),
    }),
  retryProfile: (id: string) => call(`/api/projects/${id}/profile/retry`, { method: "POST" }),
  markets: (id: string) => call<Markets>(`/api/projects/${id}/markets`),
  scan: (id: string) => call<{ generation_id: string; run_id: string | null }>(`/api/projects/${id}/scan`, { method: "POST" }),
  retryScan: (id: string) => call(`/api/projects/${id}/scan/retry`, { method: "POST" }),
  retryShortlist: (id: string) => call(`/api/projects/${id}/shortlist/retry`, { method: "POST" }),
  updateShortlist: (id: string) => call(`/api/projects/${id}/shortlist/update`, { method: "POST" }),
  editShortlist: (id: string, base_version: number, edit: { include?: string[]; exclude?: string[]; add?: string[] }) =>
    call<{ version: number }>(`/api/projects/${id}/shortlist`, { method: "PATCH", body: JSON.stringify({ base_version, ...edit }) }),
  place: (id: string, qloo_id: string, city: string) =>
    call<PlaceDetail>(`/api/projects/${id}/qloo/places/${encodeURIComponent(qloo_id)}?city=${city}`),
  intent: (id: string, base_version: number, change: { scope?: Scope; purpose?: Purpose; leave_out_presence?: boolean; home_country?: string | null }) =>
    call<{ version: number; notice: string }>(`/api/projects/${id}/intent`, { method: "PATCH", body: JSON.stringify({ base_version, ...change }) }),
  presence: (id: string, base_version: number, edit: { add_cities?: string[]; remove_cities?: string[]; leave_out_countries?: string[]; restore_countries?: string[] }) =>
    call<{ version: number }>(`/api/projects/${id}/presence`, { method: "PUT", body: JSON.stringify({ base_version, ...edit }) }),
  standins: (id: string, base_version: number, brands: string[]) =>
    call<{ version: number }>(`/api/projects/${id}/standins`, { method: "PUT", body: JSON.stringify({ base_version, brands }) }),
  searchBrands: (id: string, q: string) =>
    call<{ query: string; results: { qloo_id: string; name: string; short_description: string }[] }>(`/api/projects/${id}/qloo/brands?q=${encodeURIComponent(q)}`),
  removeAudience: (id: string, base_version: number, remove: string[]) =>
    call<{ version: number }>(`/api/projects/${id}/audience`, { method: "PUT", body: JSON.stringify({ base_version, remove }) }),
  retryAudience: (id: string) => call(`/api/projects/${id}/audience/retry`, { method: "POST" }),
  retryRanking: (id: string) => call(`/api/projects/${id}/ranking/retry`, { method: "POST" }),
  searchTags: (id: string, q: string) =>
    call<{ query: string; results: QlooTag[]; hidden: number }>(`/api/projects/${id}/qloo/tags?q=${encodeURIComponent(q)}`),
};

/** The project view, refreshed whenever the server appends an event (SSE resumes from the last seq). */
export function useProject(id: string) {
  const { data: project, error, reload } = useLive(id, api.project);
  return { project, error, reload };
}

/** Any view of a project, reloaded (debounced) on every server event. */
export function useLive<T>(id: string, loader: (id: string) => Promise<T>) {
  const [project, setProject] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef(false);

  useEffect(() => {
    let alive = true;
    let again = false;  // an event that arrives mid-request must not be lost: load once more afterwards
    const load = () => {
      if (pending.current) {
        again = true;
        return;
      }
      pending.current = true;
      loader(id)
        .then((p) => alive && (setProject(p), setError(null)))
        .catch((e) => alive && setError(e.message))
        .finally(() => {
          pending.current = false;
          if (again && alive) {
            again = false;
            load();
          }
        });
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

  const reload = () => loader(id).then(setProject);
  return { data: project, error, reload };
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
