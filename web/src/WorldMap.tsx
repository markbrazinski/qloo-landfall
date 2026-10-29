import { useEffect, useMemo, useState } from "react";
import { geoEquirectangular, geoGraticule10, geoNaturalEarth1, geoPath, type GeoPermissibleObjects } from "d3-geo";
import { feature } from "topojson-client";
import atlasUrl from "world-atlas/countries-110m.json?url";
import type { CityRow } from "./api";

const W = 960, H = 470;
export const STATUS_LABEL: Record<string, string> = {
  queued: "QUEUED", searching: "SEARCHING", returned: "RETURNED", empty: "EMPTY", failed: "FAILED", unresolved: "UNRESOLVED",
};

/** LF-WorldMap: world-atlas 110m (Natural Earth, public domain) bundled with the app, drawn with d3-geo. If the geometry
 *  can't load, the same dots sit on an equirectangular grid, labeled, with positions approximate (B08). */
// The outline loads once, as soon as the app opens, so the scan's map is never dots on a blank box (owner, 28 Oct 2026)
const atlas: Promise<GeoPermissibleObjects> = fetch(atlasUrl)
  .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
  .then((t) => feature(t, t.objects.countries) as GeoPermissibleObjects);
atlas.catch(() => undefined);  // a failure is shown per map below, never an unhandled rejection
let atlasReady: GeoPermissibleObjects | null = null;
atlas.then((g) => { atlasReady = g; }, () => undefined);

export const RULED_OUT: Record<string, string> = { ALREADY_HERE: "you already sell there", OUTSIDE_GOAL: "outside your goal",
  LEFT_OUT_BY_YOU: "you left it out", NOT_RANKED: "Qloo can't place it" };

export function WorldMap(props: {
  cities: CityRow[]; home?: { lat: number | null; lon: number | null }; variant: "scan" | "shortlist"; compact?: boolean;
  selected?: string | null; onSelect?: (cityId: string) => void; labels?: string[];
}) {
  const [geo, setGeo] = useState<GeoPermissibleObjects | "loading" | "failed">(atlasReady ?? "loading");
  useEffect(() => {
    let alive = true;
    atlas.then((g) => alive && setGeo(g), () => alive && setGeo("failed"));
    return () => { alive = false; };
  }, []);
  const failed = geo === "failed";
  const projection = useMemo(() => (failed ? geoEquirectangular() : geoNaturalEarth1()).fitExtent([[8, 8], [W - 8, H - 8]], { type: "Sphere" }),
    [failed]);
  const path = geoPath(projection);
  const labels = new Set([...(props.labels || []), ...(props.selected ? [props.selected] : [])]);
  const homeXY = props.home?.lat != null && props.home?.lon != null ? projection([props.home.lon, props.home.lat]) : null;
  const focusable = !props.compact && !!props.onSelect;
  return (
    <figure className={`world-map ${props.compact ? "world-map-compact" : ""}`}>
      {failed && <figcaption className="mono steel map-note">Map outline unavailable · positions approximate</figcaption>}
      <svg viewBox={`0 0 ${W} ${H}`} role="group" aria-label={`Map of ${props.cities.length} cities`}>
        <path d={path({ type: "Sphere" }) || ""} className="map-sphere" />
        {failed ? <path d={path(geoGraticule10()) || ""} className="map-grid" />
          : geo !== "loading" && <path d={path(geo) || ""} className="map-land" />}
        {homeXY && (
          <g className="map-home" transform={`translate(${homeXY[0]},${homeXY[1]})`} aria-label="Your home market">
            <rect x={-5} y={-5} width={10} height={10} /><text x={8} y={-6}>HOME</text>
          </g>
        )}
        {props.cities.map((c) => {
          const xy = projection([c.lon, c.lat]);
          if (!xy) return null;
          // owner, 28 Oct 2026: on the scan, cities ruled out of the ranking (already sold there, home…) are faded
          const out = props.variant === "scan" && !!c.excluded && c.excluded !== "NOT_RANKED";  // known only after the scan
          const state = props.variant === "shortlist" ? (c.pick?.included ? "proposed" : c.pick ? "excluded" : c.status) : out ? "ruledout" : c.status;
          const label = `${c.name} · ${out ? `ruled out: ${RULED_OUT[c.excluded!] ?? "not ranked"}` : STATUS_LABEL[c.status]}${c.pick?.included ? " · in research" : ""}`;
          const act = () => props.onSelect?.(c.city_id);
          return (
            <g key={c.city_id} transform={`translate(${xy[0]},${xy[1]})`} className={`map-dot dot-${state} ${props.selected === c.city_id ? "dot-selected" : ""}`}
               role={props.onSelect ? "button" : undefined} tabIndex={focusable ? 0 : undefined} aria-label={label}
               aria-pressed={props.onSelect ? props.selected === c.city_id : undefined}
               onClick={act} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); act(); } }}>
              <title>{label}</title>
              <circle r={props.compact ? 5 : 6} />
              {labels.has(c.city_id) && !props.compact && <text x={9} y={4}>{c.name}</text>}
            </g>
          );
        })}
      </svg>
    </figure>
  );
}
