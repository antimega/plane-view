"""Spherical geometry, sight lines and the sun - vectorised with numpy.

Sight-line constants: Earth radius 6,371,008.8 m and standard refraction k = 0.13 (effective radius R / (1 - k)).
sun_az_el is the NOAA approximation, vectorised.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np

R = 6371008.8
R_KM = R / 1000
K_REFR = 0.13
R_EFF = R / (1 - K_REFR)
FT = 0.3048


def xyz(lat, lon):
    """Unit vectors (..., 3)."""
    la, lo = np.radians(lat), np.radians(lon)
    return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=-1)


def latlon(v):
    v = np.asarray(v, float)
    n = np.linalg.norm(v, axis=-1)
    return np.degrees(np.arcsin(np.clip(v[..., 2] / n, -1, 1))), np.degrees(np.arctan2(v[..., 1], v[..., 0]))


def dist_km(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = p2 - p1
    dl = np.radians(np.asarray(lon2, float) - np.asarray(lon1, float))
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * R_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def bearing(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dl = np.radians(np.asarray(lon2, float) - np.asarray(lon1, float))
    y = np.sin(dl) * np.cos(p2)
    x = np.cos(p1) * np.sin(p2) - np.sin(p1) * np.cos(p2) * np.cos(dl)
    return (np.degrees(np.arctan2(y, x)) + 360) % 360


def destination(lat, lon, brg, d_km):
    d = np.asarray(d_km, float) / R_KM
    la, lo, b = np.radians(lat), np.radians(lon), np.radians(brg)
    la2 = np.arcsin(np.sin(la) * np.cos(d) + np.cos(la) * np.sin(d) * np.cos(b))
    lo2 = lo + np.arctan2(np.sin(b) * np.sin(d) * np.cos(la), np.cos(d) - np.sin(la) * np.sin(la2))
    return np.degrees(la2), (np.degrees(lo2) + 540) % 360 - 180


def wrap180(a):
    return (np.asarray(a, float) + 540) % 360 - 180


def slerp_path(lat, lon, t, t_new):
    """Interpolate a time-stamped path at new times along great circles (long gaps over oceans stay on the sphere)."""
    lat, lon, t, t_new = map(lambda a: np.asarray(a, float), (lat, lon, t, t_new))
    V = xyz(lat, lon)
    i = np.clip(np.searchsorted(t, t_new) - 1, 0, len(t) - 2)
    span = np.maximum(t[i + 1] - t[i], 1e-9)
    f = np.clip((t_new - t[i]) / span, 0, 1)
    a, b = V[i], V[i + 1]
    om = np.arccos(np.clip(np.sum(a * b, -1), -1, 1))
    so = np.sin(om)
    small = so < 1e-9
    wa = np.where(small, 1 - f, np.sin((1 - f) * om) / np.where(small, 1, so))
    wb = np.where(small, f, np.sin(f * om) / np.where(small, 1, so))
    return latlon(wa[:, None] * a + wb[:, None] * b)


def gc_points(lat1, lon1, lat2, lon2, n):
    """n points along the great circle, endpoints included."""
    a, b = xyz(lat1, lon1), xyz(lat2, lon2)
    om = math.acos(max(-1.0, min(1.0, float(a @ b))))
    f = np.linspace(0, 1, n)
    if om < 1e-12:
        return np.full(n, lat1), np.full(n, lon1)
    v = (np.sin((1 - f) * om)[:, None] * a + np.sin(f * om)[:, None] * b) / math.sin(om)
    return latlon(v)


def cross_track_km(lat, lon, lat1, lon1, lat2, lon2):
    """Signed distance of points from the great circle 1->2 (positive = right of the direction of travel)."""
    n = np.cross(xyz(lat1, lon1), xyz(lat2, lon2))
    n = n / np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-15)
    return -np.arcsin(np.clip(np.sum(xyz(lat, lon) * n, axis=-1), -1, 1)) * R_KM


def depression_deg(h_m, z_m, d_km):
    """Angle below the horizontal from height h to a point at height z, ground distance d (curvature + refraction)."""
    d = np.asarray(d_km, float) * 1000
    return np.degrees(np.arctan2(np.asarray(h_m, float) - z_m + d * d / (2 * R_EFF), np.maximum(d, 1.0)))


def horizon_km(h_m):
    """Distance to the visible (refracted) horizon of the sea surface from height h."""
    return np.sqrt(2 * R_EFF * np.maximum(np.asarray(h_m, float), 0)) / 1000


def dip_deg(h_m):
    """How far the sea horizon lies below the horizontal from height h."""
    return np.degrees(np.sqrt(2 * np.maximum(np.asarray(h_m, float), 0) / R_EFF))


def sun_az_el(t, lat, lon):
    """Sun azimuth and elevation (degrees) for unix times and positions (arrays broadcast)."""
    t = np.asarray(t, float)
    jd = t / 86400 + 2440587.5
    n = jd - 2451545.0
    L = (280.460 + 0.9856474 * n) % 360
    g = np.radians((357.528 + 0.9856003 * n) % 360)
    lam = np.radians(L + 1.915 * np.sin(g) + 0.020 * np.sin(2 * g))
    eps = np.radians(23.439 - 0.0000004 * n)
    ra = np.arctan2(np.cos(eps) * np.sin(lam), np.cos(lam))
    dec = np.arcsin(np.sin(eps) * np.sin(lam))
    gmst = (18.697374558 + 24.06570982441908 * n) % 24
    ha = np.radians((gmst * 15 + np.asarray(lon, float)) - np.degrees(ra))
    la = np.radians(lat)
    el = np.arcsin(np.sin(la) * np.sin(dec) + np.cos(la) * np.cos(dec) * np.cos(ha))
    az = np.arctan2(-np.sin(ha), np.tan(dec) * np.cos(la) - np.sin(la) * np.cos(ha))
    return (np.degrees(az) + 360) % 360, np.degrees(el)


def utc(t: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(t, dt.UTC)
