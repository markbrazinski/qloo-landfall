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
  products?: { route: string; mcp: { state: string; reason?: string }; items: ProductItem[]; attempts: { url: string; outcome: string }[] } | null;
};
export type ProductItem = { id: string; version: number; title: string; product_url: string; snapshot_id: string; price_text: string | null;
  category_terms: string[]; materials: string[]; discovered_via: string };
export type ProductSnapshot = ProductItem & { snapshot: { url: string; final_url: string; fetched_at: string; title: string; replayed: boolean; excerpt: string } };
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
  countries: { iso: string; name: string; sources: PresenceSource[]; leave_out: boolean; present: boolean }[];
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
  ceiling_by: "DEFAULT" | "YOU_SAID" | null;
};
export type Beachhead = {
  city_id: string; name: string; country: string; at: string; by: "founder"; recommended: boolean; notes: string[];
  chosen_under: { assessment_set: { id: string; version: number } | null; founder_input_version: number }; shortlist_version: number;
};
export type Project = {
  id: string; url: string; site: string; mode: string; demo: boolean;
  intent: Intent | null; goal: Goal | null; beachhead: Beachhead | null;
  plan?: { city_id: string; version: number; signed_off: boolean; changed_since_sign_off?: boolean; methods?: number;
    counts?: Record<string, number> | null } | null;
  run: { id: string; kind: string; status: string; message: string } | null;
  reading: Row[]; brief: Brief | null; activity: ActivityEntry[];
  identity: Identity; profile: Profile | null; markets_reason: string;
  markets: { stage: "none" | "ready" | "scanning" | "scanned"; reason: string; outdated?: boolean; shortlisted?: number | null };
  opportunities: { stage: "none" | "researching" | "researched"; cities?: number };
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
export type Pick = { city_id: string; included: boolean; added_by_founder: boolean; featured: boolean; kept_as_beachhead?: boolean };
export type CityRow = {
  city_id: string; name: string; country: string; region: string; lat: number; lon: number;
  status: "queued" | "searching" | "returned" | "empty" | "failed" | "unresolved";
  digest: Digest | null; fit: Fit; fit_label: string; fit_line: string; finding: string | null; reason: string | null;
  attempts: number | null; origin: string | null; proposed: boolean;
  /** what the city's investigations kept, in plain words (owner, 28 Oct 2026) */
  found: { state: "found" | "researching" | "queued" | "none" | "not_started"; line: string };
  deferred: { reason: string; line: string } | null; pick: Pick | null;
  excluded: "ALREADY_HERE" | "OUTSIDE_GOAL" | "LEFT_OUT_BY_YOU" | "NOT_RANKED" | null; entry_label: string;
  research: string | null;
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
  catalog: number; can_start: boolean; regions: string[]; auto?: boolean;
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
  beachhead: Beachhead | null; shortlist_version?: number;
};
export type PlaceDetail = {
  place: { qloo_id: string; name: string; genres: { name: string }[]; address: string | null; website: string | null;
    phone: string | null; distance_m: number | null; tags_kept: { name: string }[] };
  city: { name: string }; class: string;
  request: { signals: QSignal[]; radius_m: number; rank_note: string; origin: string; fetched_at: string };
};
export type SourceDetail = { id: string; url: string; title: string; fetched_at: string; excerpt: string; replayed: boolean };

// ---- slice 4: city research (CONTRACTS B6 · Slice 4) ----
export type SrcRef = { id: string; url: string; final_url: string; fetched_at: string; status: number; replayed: boolean };
export type CityClaim = {
  id: string; subject_name: string; method: string; text: string; state: "SUPPORTED" | "TO_CHECK" | "CONTESTED" | "STATED";
  quote: string; quote_found: boolean; locality: boolean; support: { verdict: string | null; reason: string; contradicts?: string | null };
  reason: string; source: SrcRef | null;
};
export type Lead = {
  id: string; version: number; key: string; name: string; type: string; origin: "QLOO" | "PAGE" | "YOU_SAID"; qloo_id: string | null;
  address: string | null; website: string | null; own_site: string | null; listing: boolean; chain: boolean; city_id: string;
  presence: "VERIFIED" | "NOT_CHECKED" | "SOURCES_DIFFER" | "CHECK_FAILED"; presence_check: { reason: string; source_ids: string[]; at: string; retry?: boolean };
  willingness: "UNKNOWN"; contact: { kind: string; value: string; source_id: string; source: SrcRef | null } | null;
  founder_contact: { value: string; version: number } | null; contact_version: number; claims: CityClaim[];
};
export type Partner = { id: string; name: string; presence: Lead["presence"]; chain: boolean; listing: boolean; willingness: string };
export type Approach = {
  id: string; city_id: string; method: string; status: string; name: string; concept: { name: string | null; line: string | null } | null;
  reason: string | null; reason_source: string; hypotheses: string[]; open_requirements: { text: string; state: string }[];
  lead_ids: string[]; partners: Partner[]; claims: CityClaim[]; grounding_claim_ids: string[]; decision_version: number;
  was?: string; earlier_ruled_out?: boolean;
};
export type ToolUse = { n: number; tool: string; outcome: string; source_id?: string | null; request_id?: string | null };
export type Investigation = { city_id: string; method: string | null; title: string; role: string; tools_used: ToolUse[]; status: string };
export type StreamEvent = { seq: number; at: string; city_id: string; kind: string; text: string; claim_id?: string; source_id?: string; method?: string };
export type ResearchCity = {
  city_id: string; name: string; country: string; region: string; visible: boolean;
  status: "not_started" | "queued" | "running" | "stopped" | "complete" | "partial" | "failed"; version: number | null; error: string | null;
  can: ("continue" | "retry" | "rerun")[]; selection: "RECOMMENDED" | "ADDED_BY_YOU" | null; learned: string | null; learned_source?: string;
  matrix: Record<string, { status: string; reason: string }>; approaches: Approach[]; considered: Approach[]; stale: string[];
  notes: string[]; counters: Record<string, number> | null; stream: StreamEvent[]; context_used: string | null;
  investigations: Investigation[]; previous: number | null; fenced?: boolean; fenced_reason?: string; history: Approach[];
  input_summary?: { selection: string; scan_status: string; signal: string; audiences: string[]; caveat: string | null; context: string | null; position_line: string | null };
};
export type Opportunities = {
  run: { id: string; status: string; message: string; active: boolean } | null;
  title: { approaches: number; cities: number; methods: number }; cities: ResearchCity[]; leads: Record<string, Lead>;
  methods: { key: string; label: string }[]; context: string | null; intent_version: number;
  start: { included: string[]; new: string[]; max: number; over: number; expected_usd: number; max_usd: number };
  usage: { expected: number; reserved: number }; researched: boolean;
  specialists?: Record<string, Record<string, SpecialistSummary>>;
};
export type CityEvidence = {
  id: string; url: string; final_url: string; title: string; fetched_at: string; status: number; replayed: boolean; rendered: boolean;
  excerpt: string; snapshot: string;
  claim: { id: string; label: string; value: string; state: string; quote: string; quote_found: boolean; locality: boolean;
    support: { verdict: string | null; reason: string }; reason: string };
  differ?: CityEvidence[];
};

export type Axes = {
  fit: string; grounding: { identity: string; locality: string; method_evidence: string };
  operational: { contact_route: string; willingness: string }; overall: string; reasons: string[];
};
export type Readiness = Partial<Axes> & { overall: string; reasons: string[]; candidates?: number };
export type Candidate45 = { ref: string; name: string; decision: "KEPT" | "REJECTED" | "PENDING"; reason_code: string | null;
  reason: string; override: boolean; claim_ids: string[]; lead_id?: string | null; axes: Axes | null; interpretation?: boolean };
export type Location = { state: string; lat: number | null; lon: number | null; address: string | null; source: string;
  reason: string; options?: { name: string; address: string | null; lat: number; lon: number }[]; version?: number };
export type Proximity = { center: { lat: number; lon: number }; caveat: string; anchor: { name: string; lat: number; lon: number } | null;
  placed: { lead_key: string; name: string; lat: number; lon: number; address: string | null; source: string }[];
  unplaced: { lead_key: string; name: string; state: string; reason: string; options: Location["options"] }[];
  distances: { lead_key: string; km: number; label: string; from: string }[] };
export type Attempt = { tool: string; outcome: "OK" | "EMPTY" | "FAILED" | "REFUSED"; source_id?: string | null; at: string };
export type Dossier = {
  id: string; version: number; city_id: string; method: string; status: string; error: string | null;
  concept: { name: string; line: string; source?: string }; readiness: Readiness; next_action: { kind: string; text: string; target?: string };
  candidates: Candidate45[]; missing: { text: string }[]; dependencies: { text: string; state: string }[]; faults: { field: string; reason: string }[];
  products: { id: string; version: number; title: string; product_url: string; snapshot_id: string }[];
  facts: string[]; proximity: Proximity | null; stale: string[]; counters: Record<string, number>; prompt_version: string; check_version: string;
  assumptions: string[]; workspace: { attempts: Attempt[]; open_questions: { text: string; state: string }[] } | null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  retail?: any; popup?: any; creator?: any; community?: any; digital?: any;
};
export type Draft = {
  id: string; version: number; origin: string; label: string; concept: { name: string; line: string }; activities: string[];
  success_measure: string; audience_hypothesis: { text: string; label: string; caveat: string | null };
  partners: { lead_id: string; name: string; participation: string }[]; products: { id: string; version: number; title: string; snapshot_id: string }[];
  validation_tasks: { text: string; from: string }[]; costing_inputs: { item: string; kind: string; state: string }[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  digital_outline?: any; stale: string[]; you_said?: string[]; source: { dossier_version: number; method: string; city_id: string };
  package: { id: string; version: number } | null;
  packages?: { id: string; version: number; rk: string | null; partner: { name: string } | null; status: string }[];
};
export type InvestigationView = {
  city_id: string; city: string; method: string; label: string; researched: boolean; ruled_by_note: string | null;
  cost: { cap_usd: number; expected_usd: number } | null; status: string | null; version: number | null;
  run: { id: string; status: string; message?: string } | null; can_continue: boolean;
  dossier: Dossier | null; draft: Draft | null; develop_run: { status: string; message?: string } | null;
  preferences: { workspace_id: string; version: number; include: string[]; exclude: string[]; rationale: string | null } | null; founder_creators: { id: string; canonical_url: string; handle: string; platform: string }[];
};
export type SpecialistSummary = { status: string; dossier_id: string | null; readiness: string | null; draft_id: string | null; draft_label: string | null };

// ---- slice 6: delivery (CONTRACTS B8 "Slice 6 delivery") ----
export type Segment = { field: string; text: string; state: "SUPPORTED" | "PLAIN" | "TO_CHECK" | "YOU_SAID" | "UNCHECKED"; claim_ids: string[]; cites: string[]; why?: string };
export type Money = { minor: number; ccy: string };
export type PlanValue = { field: string; value: unknown; label: "FIXED" | "FOUNDER" | "ACCOUNT" | "DERIVED"; source: string };
export type Missing = { field: string; text: string };
export type Action = { id: string; status: string; mode: string; steps: { step: string; status: string; provider_id: string | null; error?: string }[];
  ids: Record<string, string>; result: Record<string, unknown> | null; verification: { verified: boolean; objects?: Record<string, Record<string, string | null>> } | null;
  error: string | null; reference: string | null; at: string; partial: string[] | null; found: unknown[] | null; resolution: unknown };
export type DestinationState = {
  destination: string; label: string; configuration: string; summary: string; text: string; next: string[]; manual: string | null;
  prepared: string[]; usable_today: string[]; where: string; verb: string; setup_steps: string[];
  state: { adapter: string; deployment: string; missing_config: string[]; platform_access: string; access_note: string; connection: string;
    account: string | null; accounts: { id: string; name: string; currency?: string }[]; pages: { id: string; name: string }[]; package: string; missing: Missing[]; provider_validation: string;
    provider_validation_message: string | null; approval: string; approval_id: string | null; approval_use: string | null; approval_version: number | null; execution: string; simulated: boolean };
  plan: { values: PlanValue[]; blockers: Missing[]; plan_hash: string; label: string; requests: unknown[] };
  actions: Action[]; manual_records: { id: string; manual_kind: string; date: string; note: string | null }[];
  // 6.7 G3: test delivery, by label only; the configured addresses never reach the browser
  routing: { mode: "TEST_REDIRECT"; sender_label: string; recipient_label: string;
    intended: { name: string; to: string[]; cc: string[]; bcc: string[]; basis: string | null };
    receives: { subject: string; first_line: string } } | null;
  test: boolean;
};
export type PackageAsset = { artifact: { id: string; sha256: string }; role: string; alt_text: string; filename: string; media_type: string; size: number;
  source: { kind: string; url?: string; fetched_at?: string; page?: string }; rights: string; usage_constraints: string[]; readable: boolean; rights_confirmed: boolean };
export type Package = {
  id: string; version: number; flow: "outreach" | "digital"; city_id: string; method: string; concept: { name: string; line: string };
  destinations: string[]; products: { id: string; version: number; title: string }[];
  partner: { lead_id: string | null; name: string; participation: string; contact: { value: string; source_id: string | null } | null } | null;
  recipients: { to: string[]; cc: string[]; bcc: string[] }; recipients_by: Record<string, string | null>; from_name: string | null;
  assets: PackageAsset[]; asset_attempts: { product: string; url?: string; outcome: string; reason?: string }[];
  copy: { locale: string; subject?: Segment | null; segments?: Segment[]; headlines?: Segment[]; descriptions?: Segment[]; primary_text?: Segment | null;
    ad_text?: Segment | null; keywords?: { text: string; match_type: string }[]; cta?: string | null };
  evidence: { id: string; role: string; text: string; claim_id: string | null }[];
  audience_rationale: { hypothesis: string; caveat: string | null; qloo_interests: string[]; label: string };
  targeting: { geo: { name: string; radius_km: number | null; radius_by: string | null }; platform_interests: { name: string; basis: string }[] };
  budget: { amount: Money | null; source: string | null; max_cpc: Money | null; allocations?: Record<string, Money> | null; plan?: { id: string; version: number } };
  budget_view: { total_text: string | null; source: string | null; plan: { version: number } | null; split: Record<string, string | null>;
    differs_from_plan: boolean; plan_total_text: string | null } | null;
  timing: { start: string | null; end: string | null };
  creative: { display_name: string | null; cta: string | null; landing_url: string | null; landing_by: string };
  settings: { objective: string | null; currency_hint: string | null }; selection: { state: string; text: string };
  assumptions: string[]; faults: { field: string; reason: string }[]; you_said: string[]; info: Missing[]; stale: string[];
  exports: { format: string; sha256: string; package_version: number; at: string }[];
  email: { subject: string; body: string; mailto: string } | null; draft: { id: string; version: number; label: string };
  destination_states: DestinationState[]; supporting_copy: string; preparing?: { status: string; message?: string } | false;
  status?: "active" | "set_aside"; fence?: string | null; plan_ref?: { id: string; version: number } | null; copy_for?: string[];
  earlier_wording?: Record<string, unknown>; excluded?: string[]; template_ref?: { id: string; version: number } | null;
};

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { ...init, headers: { "content-type": "application/json", ...(init?.headers || {}) } });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `Request failed (${r.status})`);
  return body as T;
}

const post = (body?: object) => ({ method: "POST", body: JSON.stringify(body ?? {}) });

export const api45 = {
  investigation: (id: string, city: string, method: string) => call<InvestigationView>(`/api/projects/${id}/cities/${city}/methods/${method}`),
  investigate: (id: string, city: string, method: string, prefs: { rationale?: string; include?: string[]; exclude?: string[] } = {}) =>
    call<{ run_id: string; dossier_id: string }>(`/api/projects/${id}/cities/${city}/methods/${method}/investigate`, post(prefs)),
  resume: (id: string, city: string, method: string) => call(`/api/projects/${id}/cities/${city}/methods/${method}/continue`, post()),
  develop: (id: string, did: string, base_version: number, rationale?: string) =>
    call<{ draft_id: string }>(`/api/projects/${id}/dossiers/${did}/develop`, post({ base_version, rationale })),
  editDraft: (id: string, rid: string, base_version: number, fields: object) =>
    call<{ version: number }>(`/api/projects/${id}/drafts/${rid}`, { method: "PATCH", body: JSON.stringify({ base_version, fields }) }),
  editPrefs: (id: string, wid: string, base_version: number, edit: { include?: string[]; exclude?: string[] }) =>
    call<{ version: number }>(`/api/projects/${id}/workspaces/${wid}`, { method: "PATCH", body: JSON.stringify({ base_version, ...edit }) }),
  productSnapshot: (id: string, prid: string, version?: number) =>
    call<ProductSnapshot>(`/api/projects/${id}/products/${prid}/snapshot${version ? `?version=${version}` : ""}`),
  addCreator: (id: string, city_id: string, url: string) => call<{ lead_id: string }>(`/api/projects/${id}/leads`, post({ city_id, url })),
  chooseLocation: (id: string, lid: string, base_version: number, option: number) =>
    call<{ version: number }>(`/api/projects/${id}/leads/${lid}/location`, { method: "PUT", body: JSON.stringify({ base_version, option }) }),
};

const pkgPath = (id: string, pkg: string) => `/api/projects/${id}/packages/${pkg}`;
export const api6 = {
  prepare: (id: string, draft: string, base_version: number) =>
    call<{ package_id: string; run_id: string }>(`/api/projects/${id}/drafts/${draft}/package`, post({ base_version })),
  package: (id: string, pkg: string) => call<Package>(pkgPath(id, pkg)),
  edit: (id: string, pkg: string, base_version: number, fields: object) =>
    call<{ version: number }>(pkgPath(id, pkg), { method: "PATCH", body: JSON.stringify({ base_version, fields }) }),
  upload: (id: string, pkg: string, body: object) => call<{ version: number }>(`${pkgPath(id, pkg)}/assets`, post(body)),
  approve: (id: string, pkg: string, dst: string, base_version: number) =>
    call<{ approval_id: string; use: string }>(`${pkgPath(id, pkg)}/destinations/${dst}/approve`, post({ base_version })),
  validate: (id: string, pkg: string, dst: string) => call(`${pkgPath(id, pkg)}/destinations/${dst}/validate`, post()),
  execute: (id: string, pkg: string, dst: string, approval_id: string) =>
    call<{ status: string }>(`${pkgPath(id, pkg)}/destinations/${dst}/execute`, post({ approval_id })),
  manual: (id: string, pkg: string, dst: string, date: string) => call(`${pkgPath(id, pkg)}/destinations/${dst}/manual`, post({ date })),
  resolve: (id: string, action: string, happened: boolean, provider_id?: string) =>
    call(`/api/projects/${id}/executions/${action}/resolve`, post({ happened, provider_id })),
  connect: (id: string, dst: string, package_id: string) =>
    call<{ url?: string; simulated?: boolean }>(`/api/projects/${id}/connections/${dst}/start`, post({ package_id })),
  account: (id: string, dst: string, body: object) =>
    call(`/api/projects/${id}/connections/${dst}/account`, { method: "PUT", body: JSON.stringify(body) }),
  disconnect: (id: string, dst: string) => call(`/api/projects/${id}/connections/${dst}`, { method: "DELETE" }),
  exportUrl: (id: string, pkg: string, fmt: string, version: number, artifact?: string, preview = false) =>
    `${pkgPath(id, pkg)}/exports/${fmt}?version=${version}${artifact ? `&artifact=${artifact}` : ""}${preview ? "&preview=1" : ""}`,
};

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
  editShortlist: (id: string, base_version: number, edit: { include?: string[]; exclude?: string[]; add?: string[]; beachhead?: string | null }) =>
    call<{ version: number }>(`/api/projects/${id}/shortlist`, { method: "PATCH", body: JSON.stringify({ base_version, ...edit }) }),
  place: (id: string, qloo_id: string, city: string) =>
    call<PlaceDetail>(`/api/projects/${id}/qloo/places/${encodeURIComponent(qloo_id)}?city=${city}`),
  intent: (id: string, base_version: number, change: { scope?: Scope; purpose?: Purpose; leave_out_presence?: boolean; home_country?: string | null; ceiling_usd?: number }) =>
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
  opportunities: (id: string) => call<Opportunities>(`/api/projects/${id}/opportunities`),
  research: (id: string, body: { city_ids?: string[]; context?: string | null; refresh?: boolean }) =>
    call<{ run_id: string }>(`/api/projects/${id}/research`, { method: "POST", body: JSON.stringify(body) }),
  continueResearch: (id: string, city_ids: string[]) =>
    call<{ run_id: string }>(`/api/projects/${id}/research/continue`, { method: "POST", body: JSON.stringify({ city_ids }) }),
  retryResearch: (id: string, city_ids: string[]) =>
    call<{ run_id: string }>(`/api/projects/${id}/research/retry`, { method: "POST", body: JSON.stringify({ city_ids }) }),
  researchNote: (id: string, base_version: number, research_context: string) =>
    call<{ version: number; notice: string }>(`/api/projects/${id}/intent`, { method: "PATCH", body: JSON.stringify({ base_version, research_context }) }),
  ruleOut: (id: string, aid: string, base_version: number, undo = false) =>
    call<{ version: number }>(`/api/projects/${id}/approaches/${aid}/${undo ? "undo" : "rule-out"}`, { method: "POST", body: JSON.stringify({ base_version }) }),
  lead: (id: string, lid: string) => call<Lead>(`/api/projects/${id}/leads/${lid}`),
  leadContact: (id: string, lid: string, base_version: number, contact: string) =>
    call<{ version: number }>(`/api/projects/${id}/leads/${lid}/contact`, { method: "POST", body: JSON.stringify({ base_version, contact }) }),
  leadCheck: (id: string, lid: string) => call<{ run_id: string }>(`/api/projects/${id}/leads/${lid}/check`, { method: "POST" }),
  cityEvidence: (sid: string, claim: string) => call<CityEvidence>(`/api/sources/${sid}?claim=${claim}`),
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
    // owner, 29 Oct 2026 ("it was stuck, why did it need a refresh"): events sent while the stream was down (a server
    // restart) are never replayed, so reload on every reconnect, and check every 15 s in case one is missed anyway
    let opened = false;
    es.onopen = () => { if (opened) load(); opened = true; };
    const poll = window.setInterval(() => document.visibilityState === "visible" && load(), 15000);
    return () => {
      alive = false;
      es.close();
      clearTimeout(timer);
      clearInterval(poll);
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

// ---- slice 6.5: Plan, Act, mockups and templates (CONTRACTS B10); slice 6.6: creators (B11) ----
export type Role = "MAIN" | "SUPPORTS" | "OPTIONAL" | "NOT_NOW";
export type PlanMethod = {
  method: string; label: string; matrix: { status: string; reason: string } | null;
  investigation: { status: string; dossier: { id: string; version: number } | null; readiness: string | null; stale: string[] | null;
    found: { kept: string[]; concept: string | null } | null };
  role: { role: Role; by: string; label: string; note: string | null; dossier: { id: string; version: number } | null;
    draft: { id: string; version: number } | null } | null;
  draft: { id: string; version: number; success_measure: string; label: string; built_on: number } | null;
  why_it_fits: { text: string; refs?: string[] }[] | string[]; costing_inputs: { item: string; kind: string; state: string }[];
  dependencies: { text: string; state: string }[]; validation_tasks: { text: string; from: string }[];
  money: MethodMoney | null; launch_test: LaunchTestSection | null;
};
// ---- slice 5: money, the proposal and the digital scenario (BUILD-PLAN "Slice 5" §5.4) ----
/** Minor-unit exponent per currency: the same table as landfall/money.py EXPONENT (REVIEW-SLICE-05 R3). */
export const ccyExp = (ccy: string) => (["JPY", "KRW", "CLP", "VND", "ISK", "PYG", "UGX"].includes(ccy) ? 0 : ["KWD", "BHD", "OMR", "JOD", "TND"].includes(ccy) ? 3 : 2);
/** Money → the exact decimal text the editor shows and sends back ("1.250" KWD, "100" ISK); never rounded. */
export const majorText = (m: { minor: number; ccy: string } | null | undefined) => (m ? (m.minor / 10 ** ccyExp(m.ccy)).toFixed(ccyExp(m.ccy)) : "");
export type Basis = "LISTED_RATE" | "ESTIMATE" | "ASSUMED" | "YOU_SAID" | "NOT_KNOWN" | "NOT_COSTED";
export type Flow = "expense" | "inventory" | "refundable_deposit";
export type CostLine = {
  id: string; label: string; item_ref: string | null; amount: Money | null; basis: Basis; flow: Flow; used_by: string[];
  basis_note: string | null; source_claim_ids: string[]; by: "strategist" | "founder" | "scenario" | "code";
  text: string | null; usd: Money | null; usd_text: string | null; unconverted: boolean; counts: boolean; shared: boolean;
};
export type PlanFit = "NOT_ASSESSED" | "OVER_CEILING" | "AFFORDABILITY_UNRESOLVED" | "FITS_KNOWN_COSTS";
export type Totals = {
  expense_known: Money; inventory_known: Money; deposits_known: Money; cash_out_known: Money; assumed_known: Money;
  unknown: string[]; unconverted: string[]; text: string; remaining: Money | null;
};
export type Statement = { text: string; kind: "FACT" | "ASSUMPTION"; cites: string[]; downgraded?: string };
export type Rationale = Partial<Record<"brand" | "city" | "alternative" | "known" | "assumed" | "what_would_change" | "what_we_learn" | "success", Statement[]>>;
export type ReturnEstimate = {
  units?: number | null; retail_price?: Money | null; wholesale_price?: Money | null; sell_through?: { low: number; high: number } | null;
  retail_price_source?: { title: string }; by?: Record<string, string>;
} | null;
export type PlanMoney = Totals & {
  lines: CostLine[]; fit: PlanFit; ceiling: Money | null; needs_fx: boolean;
  fx: { rate_date: string; text: string } | null; return_estimate: ReturnEstimate;
  gross_receipts: { low: Money; high: Money; price: string; usd: { low: Money | null; high: Money | null }; label: string } | null;
  rationale: Rationale | null; rationale_evidence: { id: string; role: string; text: string }[] | null;
  proposal: { id: string; version: number; option: string } | null;
};
export type MethodMoney = { lines: number; shared: number; not_known: number; unconverted: number; subtotal: Money; text: string; counts: boolean };
export type LaunchTestSection = { activities: string[]; assumptions: string[]; success_measure: string | null; what_would_change: string[]; line_ids: string[] };
export type ProposalOption = {
  id: "A" | "B"; methods: { method: string; role: Role; reason: string }[]; cost_lines: CostLine[]; why: string; trade_off: string;
  preview: { base_version: number; roles: { method: string; role: Role; role_by: string }[]; cost_lines: CostLine[]; money: Totals; fit: PlanFit };
};
export type PlanProposal = {
  id: string | null; version?: number; options?: ProposalOption[]; preference_order?: string[]; recommendation?: string | null;
  rationale?: Rationale; faults?: { field: string; reason: string }[]; evidence?: { id: string; role: string; text: string }[];
  adopted?: string | false | null; labels?: Record<string, string>; made_from_version?: number;
  run: { id: string; status: string; message: string | null } | null;
};
export type Range = { low: number; mid: number; high: number };
export type ScenarioPlatform = {
  platform: string; allocation: Money | null; allocation_by: "SPLIT" | "YOU_SAID";
  rates: { cpm: Record<string, string> | null; ctr: Record<string, string> | null; cvr: Record<string, string> | null; frequency: number | null };
  input_states: Record<string, string>; source: { name: string; url?: string; as_of: string; context: string } | null; basis: string;
};
export type DigitalScenario = {
  id: string | null; version: number | null; currency: string; horizon_days: number; horizon_by: string; total_daily: Money | null;
  platforms: ScenarioPlatform[]; media_spend: Money | null; label: string; platforms_offered: string[]; default_platforms: string[];
  results: { platforms: { platform: string; label: string; allocation_text: string | null; calculation: string[]; basis: string;
    outputs: Record<"impressions" | "clicks" | "conversions" | "unique_reach", Range | null>; below_minimum: { text: string } | null }[];
    aggregate: Record<string, Range | null>; note: string | null } | null;
};
export type PlanView = {
  city: { city_id: string; name: string; country: string; region: string }; beachhead: boolean;
  research: { status: string; can: string[] }; methods: PlanMethod[];
  auto: { status: string; phase: string; text: string; message: string | null; started_at: string;
    steps: { key: string; label: string; state: "done" | "running" | "waiting" | "stopped"; detail: string | null }[]; feed?: { at: string; text: string }[] } | null;
  plan: { id: string; version: number; signed_off: { at: string; by: string } | null; signed_version: number | null;
    changed_since_sign_off: boolean; stale: string[] } | null;
  ceiling: { usd: number | null; by: string | null }; money: PlanMoney | null;
  signed: { version: number; money: PlanMoney } | null; proposal: PlanProposal | null; scenario: DigitalScenario | null;
};
export type ActItem = {
  key: string; kind: "BRAND_ASSETS" | "CHOOSE_RECIPIENTS" | "OUTREACH" | "DIGITAL_SETTINGS" | "CAMPAIGN_REVIEW" | "DELIVER";
  method?: string; package?: { id: string; version: number }; partner?: string | null; lead_id?: string | null; destination?: string;
  status: string; missing: string[]; depends_on: string[]; stale: string[];
};
export type ActView = {
  plan: { id: string; version: number; signed_off: { at: string; by: string } } | null; reason?: string; plan_changed: boolean;
  items: ActItem[]; earlier_plan: { package: { id: string; version: number }; method?: string; plan_version?: number | null; reason: string }[];
  counts?: Record<string, number>;
};
export type Mockup = {
  id: string; version: number; method: string; treatment: string; frame: string; state: "QUEUED" | "GENERATING" | "READY" | "FAILED" | "OUT_OF_BUDGET";
  plan_ref: { id: string; version: number }; draft: { id: string; version: number };
  overlay: { brand: string | null; city: string; concept: { name: string; line: string | null } };
  products: { id: string; version: number; title: string; artifact: { sha256: string; media_type: string } | null }[];
  image: { artifact: { sha256: string }; media_type: string } | null; error: { reason: string; retryable: boolean } | null;
  alt_text: string; stale: string[]; earlier_plan: boolean; reused_from?: { id: string; version: number } | null;
};
export type Template = { id: string; version: number; method: string; name: string; subject: string; body: string; origin: string; status: string };
export type CreatorAccount = {
  user_id: string; username: string | null; full_name: string | null; followers: number | null; engagement_percent: number | null;
  audience_location: string | null; creator_location: string | null; profile_url: { value: string; built: boolean } | null;
};
type Cost = { requests: number; credits: number; ok: boolean };
export type CreatorsView = {
  available: boolean; reason: string | null; pool: string; left: { requests: number; credits: number }; attribution: string; demo: boolean;
  costs: { search: Cost; more: Cost; enrich: Cost };
  creator_budget_line: string; qloo_context: string[];
  searches: { id: string; version: number; platform: string; origin: "LIVE" | "REPLAYED" | "SYNTHETIC" | "CACHED"; retrieved_at: string; status: string;
    note: string | null; total: number | null; request: { page: number; location: string | null; location_means: "AUDIENCE" | "CREATOR" | null };
    error: { status: number; detail: string } | null;
    accounts: CreatorAccount[]; next_page: boolean }[];
  chosen: { lead_id: string; key: string; name: string; contact: { value: string; email_status: string } | null; enrichment: string | null;
    creator_location: { state: string; text: string | null } | null; audience_location: { text: string } | null;
    found_by: { search: { id: string }; account_index: number } }[];
};

const proj = (id: string) => `/api/projects/${id}`;
export const api65 = {
  plan: (id: string, city: string) => call<PlanView>(`${proj(id)}/plans/${city}`),
  savePlan: (id: string, city: string, base_version: number | null, methods: { method: string; role: Role }[]) =>
    call<{ version: number }>(`${proj(id)}/plans/${city}`, { method: "PUT", body: JSON.stringify({ base_version, methods }) }),
  signOff: (id: string, city: string, base_version: number, expect: Record<string, { dossier_version: number; draft_version: number } | number>) =>
    call<{ version: number }>(`${proj(id)}/plans/${city}/sign-off`, post({ base_version, expect })),
  act: (id: string, city: string) => call<ActView>(`${proj(id)}/plans/${city}/act`),
  editLine: (id: string, city: string, line: string, base_version: number, edit: { amount?: { amount: string; ccy: string } | null; flow?: Flow; used_by?: string[] }) =>
    call<{ version: number }>(`${proj(id)}/plans/${city}/cost-lines/${line}`, { method: "PATCH", body: JSON.stringify({ base_version, ...edit }) }),
  editReturn: (id: string, city: string, base_version: number, edit: Record<string, unknown>) =>
    call<{ version: number }>(`${proj(id)}/plans/${city}/return`, { method: "PATCH", body: JSON.stringify({ base_version, ...edit }) }),
  retryRates: (id: string, city: string, base_version: number) =>
    call<{ version: number; notice: string }>(`${proj(id)}/plans/${city}/fx/retry`, post({ base_version })),
  autoPlan: (id: string, city: string) => call<{ run_id: string | null }>(`${proj(id)}/plans/${city}/auto`, post({})),
  propose: (id: string, city: string, dry_run = false) =>
    call<{ run_id: string | null; est: { calls: string; expected_usd: number; max_usd: number } }>(`${proj(id)}/plans/${city}/propose`, post({ dry_run })),
  adopt: (id: string, city: string, prop: string, proposal_version: number, option: string, base_version: number) =>
    call<{ version: number }>(`${proj(id)}/plans/${city}/proposals/${prop}/adopt`, post({ proposal_version, option, base_version })),
  saveScenario: (id: string, city: string, base_version: number, scenario_version: number | null, inputs: Record<string, unknown>) =>
    call<{ version: number }>(`${proj(id)}/plans/${city}/scenario`, { method: "PUT", body: JSON.stringify({ base_version, scenario_version, inputs }) }),
  confirmRights: (id: string, city: string) => call<{ packages: string[] }>(`${proj(id)}/plans/${city}/rights`, post()),
  mockups: (id: string, city: string) => call<Mockup[]>(`${proj(id)}/plans/${city}/mockups`),
  makeMockups: (id: string, city: string, methods?: string[], dry_run = false) =>
    call<{ run_id: string | null; est: { mockups: number; new_scenes: number; est_usd: number } }>(`${proj(id)}/plans/${city}/mockups`, post({ methods, dry_run })),
  mockupImage: (id: string, m: Mockup) => `${proj(id)}/mockups/${m.id}/image?version=${m.version}`,
  artifact: (id: string, sha: string) => `${proj(id)}/artifacts/${sha}`,
  prepare: (id: string, draft: string, partner_lead_id?: string) =>
    call<{ package_id: string; run_id: string | null }>(`${proj(id)}/drafts/${draft}/package`, post({ partner_lead_id })),
  setAside: (id: string, pkg: string, base_version: number, reason: "deselected" | "replaced") =>
    call<{ version: number }>(`${proj(id)}/packages/${pkg}/set-aside`, post({ base_version, reason })),
  wording: (id: string, pkg: string, base_version: number, field: string, choice: "mine" | "new") =>
    call<{ version: number }>(`${proj(id)}/packages/${pkg}/wording`, post({ base_version, field, choice })),
  templates: (id: string) => call<Template[]>(`${proj(id)}/templates`),
  saveTemplate: (id: string, body: { method: string; name?: string; subject: string; body: string }) =>
    call<{ id: string; version: number }>(`${proj(id)}/templates`, post(body)),
  editTemplate: (id: string, tid: string, base_version: number, edit: { name?: string; subject?: string; body?: string; status?: string }) =>
    call<{ id: string; version: number }>(`${proj(id)}/templates/${tid}`, { method: "PUT", body: JSON.stringify({ base_version, ...edit }) }),
  applyTemplate: (id: string, pkg: string, base_version: number, template: { id: string; version: number }) =>
    call<{ version: number; missing: string[] }>(`${proj(id)}/packages/${pkg}/apply-template`, post({ base_version, template })),
  withdraw: (id: string, pkg: string, dst: string, record_id: string) =>
    call<{ version: number }>(`${proj(id)}/packages/${pkg}/destinations/${dst}/manual/withdraw`, post({ record_id })),
  creators: (id: string, city: string) => call<CreatorsView>(`${proj(id)}/plans/${city}/creators`),
  searchCreators: (id: string, city: string, body: { platform?: string; more_of?: string; dry_run?: boolean }) =>
    call<{ search_id?: string; est?: { requests: number; credits: number }; left?: { requests: number; credits: number }; unavailable?: string | null }>(
      `${proj(id)}/plans/${city}/creators/search`, post(body)),
  chooseCreator: (id: string, sid: string, i: number) => call<{ lead_id: string }>(`${proj(id)}/creators/searches/${sid}/accounts/${i}/choose`, post()),
  enrich: (id: string, lid: string) => call<{ lead_id: string; contact: { value: string } | null; packages: string[] }>(`${proj(id)}/leads/${lid}/enrich`, post()),
};

// ---- v4 Plan ----
/** Additive fields on GET /plans/{city} (v4 08, B03/B04): each method's draft concept and activities, the found concept's name. */
export type PlanDraftV4 = NonNullable<PlanMethod["draft"]> & { concept: { name: string; line: string }; activities: string[] };
export const draftOf = (m: PlanMethod) => m.draft as PlanDraftV4 | null;
export const costItemsOf = (m: PlanMethod) => (m as PlanMethod & { cost_items?: string[] }).cost_items ?? [];
export const foundNameOf = (m: PlanMethod) => (m.investigation.found as { name?: string | null } | null)?.name ?? null;

// ---- v4 Brand ----
/** What the brand read reached, per page (`brief.read`); `unreadable` → B02 recovery even though a brief exists. */
export type BriefRead = {
  pages_read: number; pages_tried: number; pages_failed: number; why: string | null; unreadable: boolean;
  pages: { url: string; ok: boolean; why: string | null }[];
};
export const readOf = (b: Brief) => (b as Brief & { read?: BriefRead }).read;

// ---- v4 Act ----
/** B13: a live attempt rejected after some objects were created; they stay, and only the rest can be approved again. */
export type Partly = { created: string[]; missing: string[]; paused: boolean; error: string | null };
export type DestinationV4 = DestinationState & { partly?: Partly | null };

// ---- v4 Prepare ----
/** Read-time inputs of the page-rendered launch-kit tiles (landfall/mockups.py kit_of; D3a). */
export type MockupKit = {
  products: { title: string; artifact: { sha256: string; media_type: string } | null }[];
  copy: { headlines: string[]; primary_text: string | null; cta: string | null } | null; partner: string | null;
  image_why?: string | null;
};
export type KitMockup = Mockup & { kit?: MockupKit; looks?: string[][] | null };

// ---- estimates ----
/** Landfall's estimate beside a NOT KNOWN line (landfall/estimates.py): base × quantity × country price level, never the
 * line's amount. `sources` names the planning base (PLANNING_ASSUMPTION) and the World Bank price level (SOURCED). */
export type CostEstimate = {
  amount: Money; low: Money; high: Money; text: string; range_text: string; basis_note: string; label: string;
  sources: { title: string; kind: "PLANNING_ASSUMPTION" | "SOURCED"; url?: string; license?: string; retrieved_at?: string }[];
};
/** Known cash out plus every estimate (USD); `cash_out_known` never includes estimates. */
export type EstimatedTotal = { amount: Money; low: Money; high: Money; text: string; range_text: string; not_estimated: number };
export const estimateOf = (l: CostLine) => (l as CostLine & { estimate?: CostEstimate | null }).estimate ?? null;
export const estimatedTotal = (m: PlanMoney | null | undefined) => (m as (PlanMoney & { estimated_total?: EstimatedTotal | null }) | null | undefined)?.estimated_total ?? null;
/** "Use this estimate": the server recomputes it and writes the line as the founder's ESTIMATE (Plan v+1). */
export const acceptEstimate = (id: string, city: string, line: string, base_version: number) =>
  call<{ version: number }>(`${proj(id)}/plans/${city}/cost-lines/${line}`, { method: "PATCH", body: JSON.stringify({ base_version, use_estimate: true }) });

// ---- qloo community ----
/** The Qloo city target behind a community or creator investigation (landfall/specialist.py `target_view`): who
 * (audiences), where (heatmap cells, named by the neighborhoods Qloo files its own places under), which venues, and
 * local themes. Every value is Qloo's, ordered only inside its own request: a cultural relationship, not demand. */
export type QlooCell = { lat: number; lon: number; affinity: number; rank: number; hood?: string };
export type QlooVenue = { name: string; rank: number; of: number; hood: string | null; address: string | null; website: string | null;
  lat: number | null; lon: number | null; code: string };
export type QlooTarget = {
  id: string; version: number; state: "ok" | "partial"; city: string; brand: string | null; label: string | null; caveat: string;
  center: { lat: number; lon: number }; cells: QlooCell[]; cell_count: number;
  neighbourhoods: { name: string; best_rank: number; cells: number }[]; themes: { name: string; rank: number }[]; failed: string[];
  audiences: { name: string; type: string | null; rank: number; of: number; venue_state: string; flat: boolean; venues: QlooVenue[] }[];
};
/** One "Why: Qloo" line; `about` says what it explains (group, creator, venue, lead, neighborhood, audiences, themes, city). */
export type QlooWhy = { about: string; subject: string | null; text: string; refs: string[] };
/** A name a search or page in this run gave that no check confirmed yet: shown to the founder, never evidence. */
export type QlooLead = { name: string; state: "LEAD"; found: string; url: string | null; reason: string; anchor: string | null;
  neighbourhood: string | null; why_qloo: string | null };
export const qlooOf = (d: Dossier) => d as Dossier & { qloo?: QlooTarget | null; qloo_why?: QlooWhy[] };
/** The plan card's line: the most specific Qloo reason the investigation recorded. */
export const qlooLine = (why: PlanMethod["why_it_fits"]) =>
  (why as ({ text: string; refs?: string[] } | string)[]).find((x): x is { text: string; refs?: string[] } => typeof x !== "string" && !!x.refs?.includes("QLOO"))?.text ?? null;

// ---- enrichment ----
/** `brief.read.enrichment` (landfall/brand.py filled_in): what limited the reader and what filled in, said frankly, and
 * each fact's origin: SOURCE (the brand's own page) · FEED (the store's product feed) · WEB (a page the web search cited). */
export type Enrichment = {
  frank: string | null; limited: number; filled: string[]; feed_products: number; web_sources: number;
  origins: Record<string, "SOURCE" | "FEED" | "WEB">; stopped: string | null; searches: number; leads: number;
};
export const enrichOf = (b: Brief) => (readOf(b) as (BriefRead & { enrichment?: Enrichment }) | undefined)?.enrichment ?? null;
/** A product's saved photo (FETCHED artifact), or why there is none yet. */
export const photoOf = (x: ProductItem) => x as ProductItem & { image?: { sha256: string; media_type: string } | null; image_why?: string | null };
/** G-J: the owner's shared test connection, when this project uses it (judges never connect, choose or disconnect it). */
export type Shared = { available: boolean; why: string | null; disclosure: string };
export const sharedOf = (d: DestinationState) => (d as DestinationState & { shared?: Shared | null }).shared ?? null;

// ---- access tracks (DEPLOYMENT-PLAN A1) ----
/** This browser's track: "live" for a judge or owner link (or local development), "demo" for the recorded demo. */
export type SessionTrack = { track: "live" | "demo"; grant_kind: "judge" | "owner" | null; demo_cases: string[] };
export const sessionTrack = () => call<SessionTrack>("/api/session");

// ---- Launch (owner, 28 Oct 2026): one checklist, one sign-off per item (landfall/delivery.py launch_view) ----
export type LaunchOption = {
  key: string; lead_id: string | null; search?: { id: string; index: number }; name: string; source: "RESEARCH" | "INFLUENCERS_CLUB" | "YOU_SAID";
  route: { kind: "email" | "profile" | "phone"; value: string; platform?: string; by?: string }; why: string | null;
  followers?: number | null; engagement_percent?: number | null; audience_location?: string[] | string | null;
};
export type LaunchItem = {
  method: string; label: string; role: "MAIN" | "SUPPORTS"; draft: { id: string; version: number };
  packages: { id: string; version: number }[]; preparing: boolean; failed: boolean;
  options?: LaunchOption[]; preview?: { to: string; subject: string; body: string };
  creators?: { available: boolean; reason: string | null; searching: boolean; searched: boolean; status: string | null; note: string | null;
    enrich_ok: boolean; attribution: string };
  variants?: { index: number; headline: string; description: string | null; to_check: string[] }[]; picked?: number | null;
  styled?: string[];
};
export type LaunchSettings = {
  sender_name: string | null; budget: Money | null; radius_km: number | null; start: string | null; end: string | null; budget_text: string | null;
  by: Record<"sender_name" | "budget" | "radius_km" | "start" | "end", string>; version: number | null;
};
export type LaunchView = {
  plan: { id: string; version: number; signed_off: { at: string; by: string } } | null; reason?: string; plan_changed: boolean;
  settings: LaunchSettings | null; items: LaunchItem[]; earlier_plan: ActView["earlier_plan"];
};
export const apiLaunch = {
  view: (id: string, city: string) => call<LaunchView>(`${proj(id)}/plans/${city}/launch`),
  prewarm: (id: string, city: string) => call<{ started: string[]; skipped: { what: string; why: string }[] }>(`${proj(id)}/plans/${city}/launch/prewarm`, post()),
  settings: (id: string, city: string, fields: Record<string, unknown>) =>
    call<LaunchSettings>(`${proj(id)}/plans/${city}/launch/settings`, { method: "PUT", body: JSON.stringify({ fields }) }),
  pick: (id: string, city: string, body: { method: string; lead_id?: string | null; search_id?: string; index?: number }) =>
    call<{ package_id: string; run_id: string | null; note: string | null }>(`${proj(id)}/plans/${city}/launch/pick`, post(body)),
  manual: (id: string, pkg: string, dst: string, note: string) =>
    call(`${proj(id)}/packages/${pkg}/destinations/${dst}/manual`, post({ date: new Date().toISOString().slice(0, 10), note })),
};

/** Drops the "a cultural relationship, not …" caption from a backend caveat shown to the founder (owner, 28 Oct 2026);
 *  the data and prompts keep it. */
export const noCaption = (s?: string | null) => (s ?? "").replace(/:?\s*a cultural relationship, not [^)]*$/i, "").trim();
