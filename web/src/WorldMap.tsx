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
 *  can't load, the same dots sit on an equirectangular grid, labelled, with positions approximate (B08). */
export function WorldMap(props: {
  cities: CityRow[]; home?: { lat: number | null; lon: number | null }; variant: "scan" | "shortlist"; compact?: boolean;
  selected?: string | null; onSelect?: (cityId: string) => void; labels?: string[];
}) {
  const [geo, setGeo] = useState<GeoPermissibleObjects | "loading" | "failed">("loading");
  useEffect(() => {
    let alive = true;
    fetch(atlasUrl)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((t) => alive && setGeo(feature(t, t.objects.countries) as GeoPermissibleObjects))
      .catch(() => alive && setGeo("failed"));
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
          const state = props.variant === "shortlist" ? (c.pick?.included ? "proposed" : c.pick ? "excluded" : c.status) : c.status;
          const label = `${c.name} · ${STATUS_LABEL[c.status]}${c.pick?.included ? " · in research" : ""}`;
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
