"""Mesh clean-up, alignment to a canonical part frame, and vertex colouring."""
from __future__ import annotations

import logging

import numpy as np
import trimesh

from .geometry import inv_T, min_area_rect

log = logging.getLogger("partscan")


def clean_mesh(m: trimesh.Trimesh, smooth_iterations: int = 8, max_faces: int = 400_000) -> trimesh.Trimesh:
    parts = m.split(only_watertight=False)
    if len(parts) > 1:
        sizes = np.array([len(p.faces) for p in parts])
        keep = [p for p, s in zip(parts, sizes) if s >= 0.05 * sizes.max()]
        m = trimesh.util.concatenate(keep)
        log.info("removed %d small floating pieces", len(parts) - len(keep))
    if smooth_iterations:
        trimesh.smoothing.filter_taubin(m, lamb=0.5, nu=0.53, iterations=int(smooth_iterations))
    if len(m.faces) > max_faces:
        try:
            m = m.simplify_quadric_decimation(face_count=int(max_faces))
        except Exception as ex:  # fast_simplification missing
            log.warning("decimation skipped: %s", ex)
    m.remove_unreferenced_vertices()
    m.fix_normals()
    return m


def canonical_frame(m: trimesh.Trimesh):
    """Rotate about the support-plane normal (z) so the footprint's minimum-area rectangle is
    axis aligned (length along X), and centre it. Returns (T_cp: part frame -> canonical, rect)."""
    rect = min_area_rect(m.vertices[:, :2])
    a = rect["angle"]
    c, s = np.cos(-a), np.sin(-a)
    T = np.eye(4)
    T[:3, :3] = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    ctr = np.r_[rect["center"], 0.0]
    T[:3, 3] = -T[:3, :3] @ ctr
    return T, rect


def colorize(m: trimesh.Trimesh, views, max_views: int = 60):
    """Average colour of each vertex over views where it is visible.
    views: list of (rgb (H,W,3), K, T_cw, depth (h,w) or None, Kd)."""
    V = m.vertices
    N = m.vertex_normals
    acc = np.zeros((len(V), 3))
    wsum = np.zeros(len(V))
    step = max(1, len(views) // max_views)
    for rgb, K, T_cw, D, Kd in views[::step]:
        H, W = rgb.shape[:2]
        Xc = V @ T_cw[:3, :3].T + T_cw[:3, 3]
        z = Xc[:, 2]
        ok = z > 0.03
        u = K[0, 0] * Xc[:, 0] / np.where(ok, z, 1) + K[0, 2]
        v = K[1, 1] * Xc[:, 1] / np.where(ok, z, 1) + K[1, 2]
        ok &= (u >= 0) & (u <= W - 1) & (v >= 0) & (v <= H - 1)
        cam_c = inv_T(T_cw)[:3, 3]
        view = V - cam_c
        view /= np.linalg.norm(view, axis=1, keepdims=True) + 1e-12
        cosang = -(N * view).sum(1)
        ok &= cosang > 0.25
        if D is not None:
            ud = np.round(Kd[0, 0] * Xc[:, 0] / np.where(ok, z, 1) + Kd[0, 2]).astype(int)
            vdp = np.round(Kd[1, 1] * Xc[:, 1] / np.where(ok, z, 1) + Kd[1, 2]).astype(int)
            h, w = D.shape
            inside = ok & (ud >= 0) & (ud < w) & (vdp >= 0) & (vdp < h)
            dd = np.zeros(len(V))
            dd[inside] = D[vdp[inside], ud[inside]]
            ok &= inside & (np.abs(dd - z) < 0.003) & (dd > 0)
        idx = np.nonzero(ok)[0]
        if not len(idx):
            continue
        ui = np.clip(np.round(u[idx]).astype(int), 0, W - 1)
        vi = np.clip(np.round(v[idx]).astype(int), 0, H - 1)
        col = rgb[vi, ui].astype(np.float64)
        wt = cosang[idx] ** 2
        acc[idx] += col * wt[:, None]
        wsum[idx] += wt
    out = np.full((len(V), 3), 170.0)
    has = wsum > 0
    out[has] = acc[has] / wsum[has, None]
    rgba = np.concatenate([np.clip(out, 0, 255), np.full((len(V), 1), 255)], 1).astype(np.uint8)
    m.visual = trimesh.visual.ColorVisuals(m, vertex_colors=rgba)
    return float(has.mean())
