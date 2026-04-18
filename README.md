# Radii — Geodesic Circles from CSV

A QGIS plugin that draws geographically accurate (WGS-84 geodesic) circles around points defined in a CSV file.

> [!CAUTION]  
> This thing is entirely **LLM-generated**. I haven't even looked at the code. It works = it does something resembling my prompt. Triple-check the results before using them for operational purposes.

## Features

- True geodesic circles computed on the WGS-84 ellipsoid (Vincenty direct)
- Adaptive segment count — specify an accuracy tolerance and the plugin calculates the minimum vertex count to stay within it
- Accepts decimal degrees or DMS coordinates (`48 12 50.12`, `48°12'50.12"N`, `48:12:50.12`)
- Per-row color override (`rrggbb` or `#rrggbb`)
- Live file watching: edit and save your CSV in any editor; layers redraw within ~250 ms
- Parse errors shown in the QGIS message bar; the last valid layer is preserved

## Requirements

- QGIS 3.16 or later

## Installation

Copy/clone the repository into `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`.

Then in QGIS: **Plugins → Manage and Install Plugins → Installed**, tick **Radii**.

> If the plugin does not appear, enable **Settings → Show also experimental plugins** and restart QGIS.

## Usage

1. Click the **Radii** toolbar icon, or go to **Vector → Radii → Radii: geodesic circles from CSV…**
2. Browse to a CSV file (try `example.csv` from the plugin folder).
3. Set the **accuracy tolerance** — the live label shows the resulting segment count (e.g. "≈ 71 segments at r=1 km, 703 at r=100 km" at 1 m tolerance).

Two layers appear in the Layers panel:

| Layer | Contents |
|-------|----------|
| `Radii — geodesic circles` | Polygon ring for each row |
| `Radii — centers` | Point for each row |

Right-click either layer → **Zoom to Layer** to navigate to the data.

### Re-configure

Click the toolbar icon again at any time to change the tolerance, default color, or swap CSV files. Existing layers are reused rather than duplicated.

## CSV Format

```
lat,lon,height,radius,color
48.2082,16.3738,171,1500,ff0000
"48 12 50.12","16 22 25.68",180,5000,00aa00
```

| Column | Required | Description |
|--------|----------|-------------|
| `lat` | yes | Latitude — decimal (`48.2082`) or DMS (`48 12 50.12`, `48°12'50.12"N`, `48:12:50.12`) |
| `lon` | yes | Longitude — same formats as `lat` |
| `height` | no | Altitude in meters (informational only; defaults to `0`) |
| `radius` | yes | Circle radius in **meters** (must be > 0) |
| `color` | no | Fill color as `rrggbb` or `#rrggbb`; falls back to the dialog default |

A header row is detected automatically. Headerless files are also supported (column order: lat, lon, height, radius, color).

## Live Editing

Keep the dialog closed — just edit the CSV in any editor and save. The file watcher picks up changes within ~250 ms and redraws the layers. Parse errors appear in the QGIS message bar; the previous valid layer remains visible until the file is fixed.
