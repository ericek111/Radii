"""Geodesic circle generation on the WGS-84 ellipsoid.

Uses QgsDistanceArea.computeSpheroidProject() to project points along
true geodesics (direct Vincenty). Falls back to a pure-Python Vincenty
implementation if unavailable, so the module can be unit-tested outside
QGIS as well.
"""

from __future__ import annotations

import math
from typing import List, Tuple

try:
    from qgis.core import QgsDistanceArea, QgsPointXY  # type: ignore
    _HAS_QGIS = True
except Exception:  # pragma: no cover - executed outside QGIS
    _HAS_QGIS = False

# WGS-84 ellipsoid
_WGS84_A = 6378137.0
_WGS84_F = 1.0 / 298.257223563
_WGS84_B = _WGS84_A * (1 - _WGS84_F)


def segments_for_tolerance(radius_m: float, tolerance_m: float,
                           min_segments: int = 16,
                           max_segments: int = 8192) -> int:
    """Return the number of polygon segments required so that the maximum
    deviation (sagitta) between a chord and the true circular arc is at
    most ``tolerance_m``.

    For a regular N-gon inscribed in a circle of radius R the sagitta is
        s = R * (1 - cos(pi / N))
    so
        N = pi / arccos(1 - s / R)
    """
    if radius_m <= 0:
        return min_segments
    if tolerance_m <= 0:
        return max_segments
    ratio = 1.0 - tolerance_m / radius_m
    if ratio <= -1.0:
        return min_segments
    if ratio >= 1.0:
        # tolerance already satisfied by any polygon
        return min_segments
    n = math.pi / math.acos(ratio)
    n_int = int(math.ceil(n))
    return max(min_segments, min(max_segments, n_int))


def _vincenty_direct(lat_deg: float, lon_deg: float,
                     azimuth_deg: float, distance_m: float
                     ) -> Tuple[float, float]:
    """Pure-Python Vincenty direct solution on WGS-84.

    Returns (lat2_deg, lon2_deg).
    """
    a, b, f = _WGS84_A, _WGS84_B, _WGS84_F
    phi1 = math.radians(lat_deg)
    lam1 = math.radians(lon_deg)
    alpha1 = math.radians(azimuth_deg)

    sin_alpha1 = math.sin(alpha1)
    cos_alpha1 = math.cos(alpha1)

    tan_u1 = (1 - f) * math.tan(phi1)
    cos_u1 = 1.0 / math.sqrt(1 + tan_u1 * tan_u1)
    sin_u1 = tan_u1 * cos_u1

    sigma1 = math.atan2(tan_u1, cos_alpha1)
    sin_alpha = cos_u1 * sin_alpha1
    cos_sq_alpha = 1 - sin_alpha * sin_alpha
    u_sq = cos_sq_alpha * (a * a - b * b) / (b * b)
    A = 1 + u_sq / 16384 * (4096 + u_sq * (-768 + u_sq * (320 - 175 * u_sq)))
    B = u_sq / 1024 * (256 + u_sq * (-128 + u_sq * (74 - 47 * u_sq)))

    sigma = distance_m / (b * A)
    sigma_prev = 0.0
    cos2_sigma_m = 0.0
    sin_sigma = 0.0
    cos_sigma = 0.0
    for _ in range(200):
        cos2_sigma_m = math.cos(2 * sigma1 + sigma)
        sin_sigma = math.sin(sigma)
        cos_sigma = math.cos(sigma)
        delta_sigma = B * sin_sigma * (
            cos2_sigma_m
            + B / 4 * (
                cos_sigma * (-1 + 2 * cos2_sigma_m * cos2_sigma_m)
                - B / 6 * cos2_sigma_m
                * (-3 + 4 * sin_sigma * sin_sigma)
                * (-3 + 4 * cos2_sigma_m * cos2_sigma_m)
            )
        )
        sigma_prev = sigma
        sigma = distance_m / (b * A) + delta_sigma
        if abs(sigma - sigma_prev) < 1e-12:
            break

    tmp = sin_u1 * sin_sigma - cos_u1 * cos_sigma * cos_alpha1
    phi2 = math.atan2(
        sin_u1 * cos_sigma + cos_u1 * sin_sigma * cos_alpha1,
        (1 - f) * math.sqrt(sin_alpha * sin_alpha + tmp * tmp),
    )
    lam = math.atan2(
        sin_sigma * sin_alpha1,
        cos_u1 * cos_sigma - sin_u1 * sin_sigma * cos_alpha1,
    )
    C = f / 16 * cos_sq_alpha * (4 + f * (4 - 3 * cos_sq_alpha))
    L = lam - (1 - C) * f * sin_alpha * (
        sigma + C * sin_sigma * (
            cos2_sigma_m + C * cos_sigma * (-1 + 2 * cos2_sigma_m * cos2_sigma_m)
        )
    )
    lam2 = lam1 + L
    # Normalize to [-180, 180]
    lon2 = ((math.degrees(lam2) + 540) % 360) - 180
    return math.degrees(phi2), lon2


def geodesic_circle_vertices(lat: float, lon: float, radius_m: float,
                             segments: int) -> List[Tuple[float, float]]:
    """Return ``segments`` (lon, lat) vertices on a closed geodesic circle.

    The first vertex is repeated at the end to close the ring.
    """
    if segments < 3:
        segments = 3
    verts: List[Tuple[float, float]] = []
    if _HAS_QGIS:
        da = QgsDistanceArea()
        da.setEllipsoid("WGS84")
        origin = QgsPointXY(lon, lat)
        for i in range(segments):
            az = (360.0 * i) / segments
            p = da.computeSpheroidProject(origin, radius_m, math.radians(az))
            verts.append((p.x(), p.y()))
    else:
        for i in range(segments):
            az = (360.0 * i) / segments
            lat2, lon2 = _vincenty_direct(lat, lon, az, radius_m)
            verts.append((lon2, lat2))
    verts.append(verts[0])
    return verts
