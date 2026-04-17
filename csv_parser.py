"""CSV parsing with DMS/decimal coordinate support."""

import csv
import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class RadiusPoint:
    lat: float
    lon: float
    height: float
    radius: float
    color: Optional[str]
    row_index: int


class CsvParseError(Exception):
    pass


_DMS_RE = re.compile(
    r"""^\s*
        (?P<sign>[+-])?\s*
        (?P<deg>\d+(?:\.\d+)?)
        (?:[°\s:]+(?P<min>\d+(?:\.\d+)?))?
        (?:['\u2032\s:]+(?P<sec>\d+(?:\.\d+)?))?
        [\"\u2033]?
        \s*(?P<hemi>[NSEWnsew])?\s*$""",
    re.VERBOSE,
)


def parse_coordinate(raw: str) -> float:
    """Parse a coordinate string in decimal degrees or DMS.

    Accepts forms like:
        48.2134
        -48.2134
        48 12 50.12
        48 12 50.12 N
        48°12'50.12"N
        48:12:50.12
    """
    if raw is None:
        raise CsvParseError("empty coordinate")
    s = str(raw).strip()
    if not s:
        raise CsvParseError("empty coordinate")

    try:
        return float(s)
    except ValueError:
        pass

    m = _DMS_RE.match(s)
    if not m:
        raise CsvParseError(f"cannot parse coordinate: {raw!r}")

    deg = float(m.group("deg"))
    minutes = float(m.group("min") or 0.0)
    seconds = float(m.group("sec") or 0.0)
    value = deg + minutes / 60.0 + seconds / 3600.0

    sign = m.group("sign")
    hemi = (m.group("hemi") or "").upper()
    if sign == "-" or hemi in ("S", "W"):
        value = -value
    return value


_HEADER_TOKENS = {"lat", "lon", "long", "lng", "latitude", "longitude",
                  "height", "alt", "altitude", "radius", "color", "colour"}


def _looks_like_header(row) -> bool:
    cells = [str(c).strip().lower() for c in row]
    return any(c in _HEADER_TOKENS for c in cells)


def _normalize_color(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s.startswith("#"):
        s = s[1:]
    if not re.fullmatch(r"[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", s):
        raise CsvParseError(f"invalid color: {raw!r}")
    return "#" + s.lower()


def load_csv(path: str) -> List[RadiusPoint]:
    points: List[RadiusPoint] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(fh, dialect)
        header = None
        first = next(reader, None)
        if first is not None and _looks_like_header(first):
            header = [h.strip().lower() for h in first]
        elif first is not None:
            # No header — rewind by parsing the first row as data.
            try:
                points_first = _row_to_record(first, None, 1)
            except CsvParseError as exc:
                raise CsvParseError(f"row 1: {exc}") from exc
            points.append(points_first)

        for i, row in enumerate(reader, start=2 if header is not None else 2):
            if not row or all(not str(c).strip() for c in row):
                continue
            try:
                record = _row_to_record(row, header, i)
            except CsvParseError as exc:
                raise CsvParseError(f"row {i}: {exc}") from exc
            points.append(record)
    return points


def _row_to_record(row, header, idx) -> RadiusPoint:
    if header:
        mapping = {header[j]: row[j] for j in range(min(len(header), len(row)))}
        lat_raw = mapping.get("lat")
        lon_raw = mapping.get("lon") or mapping.get("long") or mapping.get("lng")
        height_raw = mapping.get("height", "0")
        radius_raw = mapping.get("radius")
        color_raw = mapping.get("color")
    else:
        if len(row) < 4:
            raise CsvParseError("expected at least lat,lon,height,radius")
        lat_raw, lon_raw, height_raw, radius_raw = row[0], row[1], row[2], row[3]
        color_raw = row[4] if len(row) > 4 else None

    if lat_raw is None or lon_raw is None or radius_raw is None:
        raise CsvParseError("missing lat/lon/radius")

    lat = parse_coordinate(lat_raw)
    lon = parse_coordinate(lon_raw)
    if not -90.0 <= lat <= 90.0:
        raise CsvParseError(f"lat out of range: {lat}")
    if not -180.0 <= lon <= 180.0:
        raise CsvParseError(f"lon out of range: {lon}")

    try:
        height = float(str(height_raw).strip()) if str(height_raw).strip() else 0.0
    except ValueError as exc:
        raise CsvParseError(f"invalid height: {height_raw!r}") from exc

    try:
        radius = float(str(radius_raw).strip())
    except ValueError as exc:
        raise CsvParseError(f"invalid radius: {radius_raw!r}") from exc
    if radius <= 0:
        raise CsvParseError(f"radius must be positive: {radius}")

    color = _normalize_color(color_raw)
    return RadiusPoint(lat=lat, lon=lon, height=height, radius=radius,
                       color=color, row_index=idx)
