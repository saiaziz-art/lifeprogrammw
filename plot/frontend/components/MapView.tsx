"use client";

import type { GeoJSONSource, Map as MlMap, MapLayerMouseEvent, StyleSpecification } from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

type FC = { type: "FeatureCollection"; features: { type: "Feature"; geometry: unknown; properties: Record<string, unknown> }[] };
type MapConfig = {
  search_areas?: { name: string; bbox: [number, number, number, number]; enabled: boolean }[];
  basemaps: { id: string; name: string; style?: string; raster?: string; attribution: string; note?: string }[];
  overlays: { id: string; name: string; type: string; url: string; layers: string; attribution: string; verified: boolean }[];
};

export type MapFilters = { minScore: number; origins: string[]; includeFatal: boolean; includeStale: boolean };

const EMPTY_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: "bg", type: "background", paint: { "background-color": "#e9ece6" } }],
};

const CONSTRAINT_COLORS: [string, string][] = [
  ["green-belt", "#7fb069"],
  ["area-of-outstanding-natural-beauty", "#c9b27c"],
  ["national-park", "#5c8a5e"],
  ["conservation-area", "#b76e79"],
  ["ancient-woodland", "#2e6b3a"],
  ["site-of-special-scientific-interest", "#4b7fa3"],
  ["special-protection-area", "#6c4a7d"],
  ["flood-risk-zone", "#3d6fb6"],
];

export function MapView({
  points,
  boundaries,
  filters,
  onSelect,
  focus,
  height,
  drawMode,
  onDraw,
  onPick,
}: {
  points: FC;
  boundaries: FC;
  filters: MapFilters;
  onSelect?: (id: number) => void;
  focus?: { lon: number; lat: number; zoom?: number } | null;
  height?: string;
  drawMode?: boolean;
  onDraw?: (coords: [number, number][]) => void;
  onPick?: (p: [number, number]) => void;
}) {
  const el = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MlMap | null>(null);
  const [cfg, setCfg] = useState<MapConfig | null>(null);
  const [base, setBase] = useState("streets");
  const [overlays, setOverlays] = useState<Record<string, boolean>>({ "flood-zones": false, geology: false, constraints: false });
  const [notice, setNotice] = useState<string | null>(null);
  const [constraintMsg, setConstraintMsg] = useState<string | null>(null);
  const [ready, setReady] = useState(0);
  const drawPts = useRef<[number, number][]>([]);

  useEffect(() => {
    api<MapConfig>("/map/config").then(setCfg).catch(() => setCfg({ basemaps: [], overlays: [] }));
  }, []);

  // create / restyle map
  useEffect(() => {
    if (!cfg || !el.current) return;
    let cancelled = false;
    let fallbackTimer: ReturnType<typeof setTimeout> | undefined;
    (async () => {
      const maplibregl = (await import("maplibre-gl")).default;
      if (cancelled) return;
      const bm = cfg.basemaps.find((b) => b.id === base);
      let style: string | StyleSpecification = EMPTY_STYLE;
      if (bm?.style) style = bm.style;
      else if (bm?.raster)
        style = { version: 8, sources: { r: { type: "raster", tiles: [bm.raster], tileSize: 256, attribution: bm.attribution } }, layers: [{ id: "r", type: "raster", source: "r" }] };
      if (!mapRef.current) {
        mapRef.current = new maplibregl.Map({ container: el.current!, style, center: [-1.75, 50.85], zoom: 8.3, attributionControl: { compact: true } });
        mapRef.current.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
        mapRef.current.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
      } else {
        mapRef.current.setStyle(style);
      }
      const m = mapRef.current;
      let loaded = false;
      let fellBack = false;
      const done = () => {
        if (loaded) return;
        loaded = true;
        setReady((n) => n + 1);
      };
      m.once("style.load", () => {
        if (!fellBack) setNotice(null);
        done();
      });
      m.on("error", (e) => {
        const msg = String((e as { error?: Error }).error?.message ?? "");
        if (!loaded && /style|Failed to fetch|NetworkError|AJAXError/i.test(msg)) {
          fellBack = true;
          setNotice("Basemap tiles could not be loaded from this network — showing sites and overlays on a plain background.");
          m.setStyle(EMPTY_STYLE);
          m.once("style.load", done);
        }
      });
      fallbackTimer = setTimeout(() => {
        if (!loaded) {
          fellBack = true;
          setNotice("Basemap is slow or unreachable — showing a plain background.");
          m.setStyle(EMPTY_STYLE);
          m.once("style.load", done);
        }
      }, 7000);
    })();
    return () => {
      cancelled = true;
      if (fallbackTimer) clearTimeout(fallbackTimer);
    };
  }, [cfg, base]);

  // data layers (re-added after every style change)
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !ready || !cfg) return;
    const filt = {
      ...points,
      features: points.features.filter((f) => {
        const p = f.properties;
        if (!filters.includeFatal && p.fatal) return false;
        if (!filters.includeStale && p.stale) return false;
        if (!filters.origins.includes(String(p.origin))) return false;
        if (filters.minScore > 0 && (Number(p.score) || 0) < filters.minScore) return false;
        return true;
      }),
    };
    const upsert = (id: string, data: FC) => {
      const s = m.getSource(id) as GeoJSONSource | undefined;
      if (s) s.setData(data as never);
      else m.addSource(id, { type: "geojson", data: data as never });
    };
    // WMS overlays first (below vectors)
    for (const ov of cfg.overlays) {
      const sid = `wms-${ov.id}`;
      if (overlays[ov.id]) {
        if (!m.getSource(sid)) {
          const url = `${ov.url}?service=WMS&request=GetMap&version=1.3.0&layers=${encodeURIComponent(ov.layers)}&styles=&format=image/png&transparent=true&crs=EPSG:3857&width=256&height=256&bbox={bbox-epsg-3857}`;
          m.addSource(sid, { type: "raster", tiles: [url], tileSize: 256, attribution: ov.attribution });
          m.addLayer({ id: sid, type: "raster", source: sid, paint: { "raster-opacity": 0.55 } });
        }
      } else if (m.getLayer(sid)) {
        m.removeLayer(sid);
        m.removeSource(sid);
      }
    }
    const areas: FC = {
      type: "FeatureCollection",
      features: (cfg.search_areas ?? []).filter((a) => a.enabled).map((a) => {
        const [x1, y1, x2, y2] = a.bbox;
        return { type: "Feature", geometry: { type: "Polygon", coordinates: [[[x1, y1], [x2, y1], [x2, y2], [x1, y2], [x1, y1]]] }, properties: { name: a.name } };
      }),
    };
    upsert("search-areas", areas);
    if (!m.getLayer("search-areas")) {
      m.addLayer({ id: "search-areas", type: "line", source: "search-areas", paint: { "line-color": "#2f5d50", "line-width": 1, "line-dasharray": [3, 3], "line-opacity": 0.5 } });
    }
    upsert("boundaries", boundaries);
    if (!m.getLayer("boundary-fill")) {
      m.addLayer({ id: "boundary-fill", type: "fill", source: "boundaries", paint: { "fill-color": "#2f5d50", "fill-opacity": 0.12 } });
      m.addLayer({ id: "boundary-line", type: "line", source: "boundaries", paint: { "line-color": "#2f5d50", "line-width": 1.5 } });
    }
    upsert("sites", filt);
    if (!m.getLayer("sites")) {
      m.addLayer({
        id: "sites",
        type: "circle",
        source: "sites",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 7, 4.5, 12, 8, 15, 11],
          "circle-color": ["case", ["get", "fatal"], "#a83b32", ["==", ["get", "score"], null], "#b8bdb9", [">=", ["get", "score"], 60], "#2f5d50", [">=", ["get", "score"], 40], "#7a8f55", "#a99566"],
          "circle-stroke-color": ["case", ["==", ["get", "origin"], "on_market"], "#ffffff", "#1c2629"],
          "circle-stroke-width": ["case", ["==", ["get", "origin"], "on_market"], 1.5, 2],
          "circle-opacity": ["case", ["get", "stale"], 0.55, 0.95],
        },
      });
      m.addLayer({ id: "site-labels", type: "symbol", source: "sites", minzoom: 12, layout: { "text-field": ["to-string", ["round", ["coalesce", ["get", "score"], 0]]], "text-size": 10, "text-allow-overlap": true }, paint: { "text-color": "#ffffff" } });
      m.on("click", "sites", (e: MapLayerMouseEvent) => {
        const id = Number(e.features?.[0]?.properties?.id);
        if (id && onSelect) onSelect(id);
      });
      m.on("mouseenter", "sites", () => (m.getCanvas().style.cursor = "pointer"));
      m.on("mouseleave", "sites", () => (m.getCanvas().style.cursor = ""));
    }
  }, [ready, points, boundaries, filters, overlays, cfg, onSelect]);

  // planning constraints (vector, fetched for the current view)
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !ready) return;
    const remove = () => {
      for (const id of ["constraints-fill", "constraints-line"]) if (m.getLayer(id)) m.removeLayer(id);
      if (m.getSource("constraints")) m.removeSource("constraints");
    };
    if (!overlays.constraints) {
      remove();
      setConstraintMsg(null);
      return;
    }
    const load = async () => {
      if (m.getZoom() < 12) {
        setConstraintMsg("Zoom in (level 12+) to load planning constraint polygons.");
        return;
      }
      const b = m.getBounds();
      try {
        const fc = await api<FC & { note?: string }>(`/map/constraints?bbox=${[b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => v.toFixed(5)).join(",")}`);
        setConstraintMsg(fc.note ?? `${fc.features.length} constraint feature(s) in view (planning.data.gov.uk).`);
        const s = m.getSource("constraints") as GeoJSONSource | undefined;
        if (s) s.setData(fc as never);
        else {
          m.addSource("constraints", { type: "geojson", data: fc as never });
          const color = ["match", ["get", "dataset"], ...CONSTRAINT_COLORS.flat(), "#888888"] as never;
          m.addLayer({ id: "constraints-fill", type: "fill", source: "constraints", paint: { "fill-color": color, "fill-opacity": 0.18 } }, m.getLayer("boundary-fill") ? "boundary-fill" : undefined);
          m.addLayer({ id: "constraints-line", type: "line", source: "constraints", paint: { "line-color": color, "line-width": 1 } }, m.getLayer("boundary-fill") ? "boundary-fill" : undefined);
        }
      } catch (e) {
        setConstraintMsg(`Constraint layer unavailable: ${(e as Error).message}`);
      }
    };
    load();
    m.on("moveend", load);
    return () => {
      m.off("moveend", load);
    };
  }, [overlays.constraints, ready]);

  // focus (re-applied once the map is ready)
  const fLon = focus?.lon, fLat = focus?.lat, fZoom = focus?.zoom;
  useEffect(() => {
    if (fLon !== undefined && fLat !== undefined && mapRef.current && ready) mapRef.current.jumpTo({ center: [fLon, fLat], zoom: fZoom ?? 15 });
  }, [fLon, fLat, fZoom, ready]);

  // simple polygon drawing (click to add vertices, double-click to finish)
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !ready || !drawMode) return;
    drawPts.current = [];
    m.doubleClickZoom.disable();
    const render = () => {
      const pts = drawPts.current;
      const fc: FC = { type: "FeatureCollection", features: pts.length > 1 ? [{ type: "Feature", geometry: { type: "LineString", coordinates: [...pts, pts[0]] }, properties: {} }] : [] };
      const s = m.getSource("draw") as GeoJSONSource | undefined;
      if (s) s.setData(fc as never);
      else {
        m.addSource("draw", { type: "geojson", data: fc as never });
        m.addLayer({ id: "draw", type: "line", source: "draw", paint: { "line-color": "#c46a3c", "line-width": 2, "line-dasharray": [2, 1] } });
      }
    };
    const click = (e: { lngLat: { lng: number; lat: number } }) => {
      drawPts.current.push([e.lngLat.lng, e.lngLat.lat]);
      render();
    };
    const dbl = () => {
      if (drawPts.current.length >= 3 && onDraw) onDraw([...drawPts.current, drawPts.current[0]]);
    };
    m.on("click", click);
    m.on("dblclick", dbl);
    return () => {
      m.off("click", click);
      m.off("dblclick", dbl);
      m.doubleClickZoom.enable();
      if (m.getLayer("draw")) m.removeLayer("draw");
      if (m.getSource("draw")) m.removeSource("draw");
    };
  }, [drawMode, ready, onDraw]);

  // single-click location picking
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !ready || !onPick) return;
    const click = (e: { lngLat: { lng: number; lat: number } }) => onPick([e.lngLat.lng, e.lngLat.lat]);
    m.on("click", click);
    m.getCanvas().style.cursor = "crosshair";
    return () => {
      m.off("click", click);
      m.getCanvas().style.cursor = "";
    };
  }, [onPick, ready]);

  useEffect(() => () => mapRef.current?.remove(), []);

  return (
    <div className="mapwrap" style={height ? { height } : undefined}>
      <div ref={el} style={{ position: "absolute", inset: 0 }} />
      <div className="card" style={{ position: "absolute", bottom: 34, right: 52, zIndex: 5, padding: "8px 10px", fontSize: 12 }}>
        <div className="row" style={{ gap: 6, marginBottom: 6 }}>
          {(cfg?.basemaps ?? []).map((b) => (
            <button key={b.id} className={`btn sm ${base === b.id ? "primary" : ""}`} onClick={() => setBase(b.id)} title={b.note ?? b.attribution}>
              {b.id === "streets" ? "Map" : "Satellite"}
            </button>
          ))}
          <button className={`btn sm ${base === "none" ? "primary" : ""}`} onClick={() => setBase("none")}>
            Plain
          </button>
        </div>
        {(cfg?.overlays ?? []).map((o) => (
          <label key={o.id} className="row" style={{ gap: 6 }} title={`${o.attribution}${o.verified ? "" : " · layer name unverified until first use"}`}>
            <input type="checkbox" checked={!!overlays[o.id]} onChange={(e) => setOverlays({ ...overlays, [o.id]: e.target.checked })} /> {o.name}
          </label>
        ))}
        <label className="row" style={{ gap: 6 }}>
          <input type="checkbox" checked={overlays.constraints} onChange={(e) => setOverlays({ ...overlays, constraints: e.target.checked })} /> Planning constraints
        </label>
        {constraintMsg ? <div className="small" style={{ maxWidth: 240 }}>{constraintMsg}</div> : null}
      </div>
      {notice ? (
        <div className="notice warn" style={{ position: "absolute", top: 12, left: "50%", transform: "translateX(-50%)", zIndex: 6, maxWidth: 520 }}>
          {notice}
        </div>
      ) : null}
      {drawMode ? (
        <div className="notice info" style={{ position: "absolute", bottom: 34, left: 12, zIndex: 6 }}>
          Click to add boundary corners; double-click to finish.
        </div>
      ) : null}
    </div>
  );
}
