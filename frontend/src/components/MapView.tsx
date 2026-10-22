import maplibregl from "maplibre-gl";
import { useEffect, useRef } from "react";

type Props = {
  geojson: GeoJSON.FeatureCollection;
  onClickXs: (xsId: string) => void;
};

// Lightweight map view (M5): show reach centerline, cross-sections, structures,
// basemap from USGS national map (no token). Click a cross-section → jump on
// the profile above.
export default function MapView({ geojson, onClickXs }: Props) {
  const mapEl = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);

  useEffect(() => {
    if (!mapEl.current) return;
    if (mapRef.current) return; // already initialised

    const map = new maplibregl.Map({
      container: mapEl.current,
      style: {
        version: 8,
        sources: {
          basemap: {
            type: "raster",
            tiles: [
              "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}",
            ],
            tileSize: 256,
            attribution: "USGS",
          },
        },
        layers: [
          { id: "basemap", type: "raster", source: "basemap" },
        ],
      },
      center: [-85.7, 40.2],
      zoom: 11,
    });

    map.on("load", () => {
      mapRef.current = map;
      map.addSource("ras", { type: "geojson", data: geojson });
      map.addLayer({
        id: "reach",
        type: "line",
        source: "ras",
        filter: ["==", ["get", "kind"], "reach"],
        layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": "#196bd6", "line-width": 2 },
      });
      map.addLayer({
        id: "xs",
        type: "line",
        source: "ras",
        filter: ["==", ["get", "kind"], "cross_section"],
        paint: { "line-color": "#555", "line-width": 1 },
      });
      map.addLayer({
        id: "struct",
        type: "line",
        source: "ras",
        filter: ["==", ["get", "kind"], "structure"],
        paint: { "line-color": "#e9c46a", "line-width": 3 },
      });
      map.fitBounds(
        bounds(geojson) as [[number, number], [number, number]],
        { padding: 20 },
      );

      map.on("click", "xs", (e: any) => {
        const f = e.features?.[0];
        if (f?.properties?.xs_id) onClickXs(String(f.properties.xs_id));
      });
    });

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, [geojson, onClickXs]);

  return <div ref={mapEl} className="map-canvas" aria-label="HEC-RAS model map" />;
}

function bounds(fc: GeoJSON.FeatureCollection): [[number, number], [number, number]] {
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  for (const f of fc.features) {
    const g: any = f.geometry;
    if (!g?.coordinates) continue;
    walk(g.coordinates);
  }
  return [[minx, miny], [maxx, maxy]];

  function walk(arr: any) {
    if (typeof arr[0] === "number") {
      const [x, y] = arr;
      if (x < minx) minx = x; if (x > maxx) maxx = x;
      if (y < miny) miny = y; if (y > maxy) maxy = y;
    } else {
      for (const sub of arr) walk(sub);
    }
  }
}
