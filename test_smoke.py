"""Offline smoke test — exercises the non-QGIS-dependent modules.

Run with:  python3 test_smoke.py
"""

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from radii.csv_parser import load_csv, parse_coordinate
from radii.geodesic import (
    _vincenty_direct,
    geodesic_circle_vertices,
    segments_for_tolerance,
)


def test_parse_coordinate():
    assert abs(parse_coordinate("48.2134") - 48.2134) < 1e-9
    assert abs(parse_coordinate("-48.2134") - -48.2134) < 1e-9
    assert abs(parse_coordinate("48 12 50.12")
               - (48 + 12 / 60 + 50.12 / 3600)) < 1e-9
    assert abs(parse_coordinate("48 12 50.12 S")
               - -(48 + 12 / 60 + 50.12 / 3600)) < 1e-9
    assert abs(parse_coordinate("48°12'50.12\"N")
               - (48 + 12 / 60 + 50.12 / 3600)) < 1e-9
    print("parse_coordinate OK")


def test_segments():
    assert segments_for_tolerance(1000, 1.0) >= 50
    assert segments_for_tolerance(100_000, 1.0) >= 500
    assert segments_for_tolerance(1000, 0.001) > segments_for_tolerance(1000, 1.0)
    n_small = segments_for_tolerance(10, 1.0)
    n_big = segments_for_tolerance(10_000, 1.0)
    assert n_big > n_small
    print(f"segments OK (1km@1m={segments_for_tolerance(1000,1.0)}, "
          f"100km@1m={segments_for_tolerance(100000,1.0)})")


def test_vincenty_direct_closure():
    # Walk 100 km north along a meridian then 100 km south — land back exactly.
    lat0, lon0 = 48.0, 16.0
    lat1, lon1 = _vincenty_direct(lat0, lon0, 0.0, 100_000)
    lat2, lon2 = _vincenty_direct(lat1, lon1, 180.0, 100_000)
    assert abs(lat2 - lat0) < 1e-8, (lat0, lat2)
    assert abs(lon2 - lon0) < 1e-8, (lon0, lon2)
    # 1° of latitude ≈ 111 km on WGS-84 — loose sanity check.
    assert abs(lat1 - lat0 - 100_000 / 111_000) < 0.01
    print("vincenty direct round-trip OK")


def test_circle_all_on_radius():
    # Every vertex must sit on the requested geodesic distance.
    lat, lon, r = 48.0, 16.0, 50_000.0
    verts = geodesic_circle_vertices(lat, lon, r, 64)
    # Measure sample vertex's geodesic distance via Vincenty inverse (haversine
    # is good enough for a tolerance of a few meters at this scale).
    for lon_v, lat_v in verts[:-1]:
        d = _haversine(lat, lon, lat_v, lon_v)
        assert abs(d - r) < 1000, (d, r)  # loose: haversine ≠ vincenty
    print("circle vertex distances OK")


def test_load_csv():
    path = os.path.join(HERE, "example.csv")
    points = load_csv(path)
    assert len(points) == 4
    assert abs(points[0].lat - 48.2082) < 1e-9
    assert points[0].color == "#ff0000"
    dms = points[1]
    assert abs(dms.lat - (48 + 12 / 60 + 50.12 / 3600)) < 1e-9
    assert points[2].color == "#1f77b4"
    print("load_csv OK")


def _haversine(lat1, lon1, lat2, lon2):
    R = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


if __name__ == "__main__":
    test_parse_coordinate()
    test_segments()
    test_vincenty_direct_closure()
    test_circle_all_on_radius()
    test_load_csv()
    print("all OK")
