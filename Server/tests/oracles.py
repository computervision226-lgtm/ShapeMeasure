"""Stand-ins for the AI models in offline tests (no GPU / no Hugging Face access here).

OracleDepth imitates PromptDA: ground-truth depth rendered for the frame, corrupted with
  per-frame scale/offset bias, a smooth low-frequency error field, noise, edge bleeding and
  (pessimistic mode) "relief compression" - the part appears flatter than it is, a typical
  failure of LiDAR-anchored depth on small objects.
OracleSegmenter imitates SAM: the true silhouette with +-1 px boundary jitter and an
  occasional gross error (chunk added or missing).
Both emulate exactly the camera model the pipeline works with (incl. its undistortion).
"""
from __future__ import annotations

import os
import sys

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import trimesh  # noqa: E402

from synth_scan import Scene, Textures  # noqa: E402
from partscan.geometry import K_for_size  # noqa: E402


class _Truth:
    def __init__(self, truth_npz):
        t = np.load(truth_npz)
        self.T = t["T_wc_part"]
        self.Ks = t["Ks"]
        self.dist = t["dist"]
        mesh = trimesh.Trimesh(t["part_vertices"], t["part_faces"], process=False)
        self.scene = Scene(mesh, Textures(None, np.random.default_rng(0)))

    def rays_for(self, f, W, H):
        """Camera rays (camera frame, z=1) for every pixel of the pipeline's image of frame f,
        resized to (W, H): emulate the pipeline's undistortion, then the true lens."""
        Wf, Hf = f.rgb_size
        Kp = K_for_size(f.K, (Wf, Hf), (W, H))
        u, v = np.meshgrid(np.arange(W, dtype=np.float64), np.arange(H, dtype=np.float64))
        x = (u - Kp[0, 2]) / Kp[0, 0]
        y = (v - Kp[1, 2]) / Kp[1, 1]
        if "undist_intr" in f.extra:
            it = f.extra["undist_intr"]
            r2 = x * x + y * y
            dd = 1 + it[3] * r2 + it[4] * r2 * r2
            Kc = f.K  # = corrected K used as source model by the pipeline
            us = Kc[0, 0] * x * dd + Kc[0, 2]  # source pixel (full res) in the ORIGINAL image
            vs = Kc[1, 1] * y * dd + Kc[1, 2]
        else:
            us = Kp[0, 0] * x + Kp[0, 2]
            vs = Kp[1, 1] * y + Kp[1, 2]
            us = (us + 0.5) * Wf / W - 0.5
            vs = (vs + 0.5) * Hf / H - 0.5
        Kt = self.Ks[f.idx]
        xd = (us - Kt[0, 2]) / Kt[0, 0]
        yd = (vs - Kt[1, 2]) / Kt[1, 1]
        xu, yu = xd.copy(), yd.copy()
        k1, k2 = self.dist
        for _ in range(10):
            r2 = xu * xu + yu * yu
            fct = 1 + k1 * r2 + k2 * r2 * r2
            xu, yu = xd / fct, yd / fct
        return np.stack([xu, yu, np.ones_like(xu)], -1).reshape(-1, 3)

    def render(self, f, W, H):
        import open3d as o3d
        d_cam = self.rays_for(f, W, H)
        T = self.T[f.idx]
        d_w = d_cam @ T[:3, :3].T
        r = np.concatenate([np.broadcast_to(T[:3, 3], d_w.shape), d_w], 1).astype(np.float32)
        ans = self.scene.sc.cast_rays(o3d.core.Tensor(r))
        t = ans["t_hit"].numpy()
        gid = ans["geometry_ids"].numpy()
        hit = np.isfinite(t)
        z = np.where(hit, t, 0.0).reshape(H, W).astype(np.float32)
        part = (hit & (gid == self.scene.part_id)).reshape(H, W)
        return z, part


class OracleDepth:
    name = "oracle-promptda"

    def __init__(self, truth_npz, scale=0.7, pessimistic=False, seed=0):
        self.tr = _Truth(truth_npz)
        self.scale = scale
        self.pess = pessimistic
        self.rng = np.random.default_rng(seed)

    def predict(self, f):
        W, H = f.rgb_size
        w, h = int(round(W * self.scale / 14) * 14), int(round(H * self.scale / 14) * 14)
        z, part = self.tr.render(f, w, h)
        rng = self.rng
        d = z.astype(np.float64)
        bg = d > 0
        d = np.where(bg, d, d[bg].max() if bg.any() else 1.0)
        if self.pess:
            # relief compression: part pulled towards the local background depth
            bgd = cv2.dilate(np.where(part, 0, d).astype(np.float32), np.ones((31, 31), np.uint8))
            bgd = np.where(part, bgd, d)
            c = rng.uniform(0.0, 0.06)
            d = np.where(part, d + c * (bgd - d), d)
        # edge bleeding (depth smeared over ~2 px), low-frequency field, per-frame affine bias, noise
        d = gaussian_filter(d, 1.2)
        field = cv2.resize(gaussian_filter(rng.normal(0, 1, (h // 32 + 2, w // 32 + 2)), 1.0), (w, h),
                           interpolation=cv2.INTER_CUBIC) * (0.0012 if self.pess else 0.0006)
        a = 1 + rng.normal(0, 0.01)
        b = rng.normal(0, 0.003)
        d = a * d + b + field + rng.normal(0, 0.0004, d.shape)
        if "valid" in f.extra:
            vm = cv2.resize(f.extra["valid"].astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST) > 0
            d = np.where(vm, d, 0)
        return d.astype(np.float32), K_for_size(f.K, (W, H), (w, h))


class OracleSegmenter:
    name = "oracle-sam"
    trusted = True

    def __init__(self, truth_npz, gross_error_rate=0.04, seed=0):
        self.tr = _Truth(truth_npz)
        self.rng = np.random.default_rng(seed + 1)
        self.p_gross = gross_error_rate

    def segment(self, f, coarse, box, pos, neg):
        W, H = f.rgb_size
        _, part = self.tr.render(f, W, H)
        m = part.astype(np.uint8)
        r = self.rng.random()
        k = np.ones((3, 3), np.uint8)
        if r < 0.25:
            m = cv2.dilate(m, k)
        elif r < 0.5:
            m = cv2.erode(m, k)
        if self.rng.random() < self.p_gross and m.any():
            ys, xs = np.nonzero(m)
            cx, cy = int(xs.mean()), int(ys.mean())
            ax = int(max(8, 0.4 * np.ptp(xs)))
            blob = np.zeros_like(m)
            cv2.ellipse(blob, (cx + int(0.5 * np.ptp(xs)), cy), (ax, ax // 2), 30, 0, 360, 1, -1)
            m = (m | blob) if self.rng.random() < 0.5 else (m & (1 - blob))
        return m.astype(bool), 0.95
