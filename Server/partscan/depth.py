"""Dense depth per keyframe + per-frame metric correction.

PromptDepthAnything (CVPR 2025) is a depth foundation model that takes the RGB image AND the
iPhone's low-resolution LiDAR map as a "prompt" and returns sharp, metric depth at up to
~1428x1904. It was trained on ARKit LiDAR data, i.e. exactly this sensor.

Even so, a single depth map can carry a few millimetres of scale/offset bias (the LiDAR
anchor). Each frame is therefore corrected with a robust affine fit against geometry that
is known more precisely from multi-view photogrammetry: the board / support plane and the
triangulated feature points.
"""
from __future__ import annotations

import logging

import cv2
import numpy as np

from .geometry import K_for_size, inv_T

log = logging.getLogger("partscan")


def fill_invalid(depth: np.ndarray) -> np.ndarray:
    """Nearest-valid fill (PromptDA min/max-normalises its prompt, so zeros must not leak in)."""
    bad = ~(depth > 0)
    if not bad.any():
        return depth
    if bad.all():
        return np.full_like(depth, 1.0)
    _, idx = cv2.distanceTransformWithLabels(bad.astype(np.uint8), cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL)
    vals = np.zeros(idx.max() + 1, depth.dtype)
    good_idx = idx[~bad]
    vals[good_idx] = depth[~bad]
    return np.where(bad, vals[idx], depth)


class LidarUpsampler:
    """No-AI fallback: bilinear upsampling of the 256x192 LiDAR map to half the RGB resolution."""
    name = "lidar"

    def __init__(self, scale: float = 0.5):
        self.scale = scale

    def predict(self, f):
        W, H = f.rgb_size
        w, h = int(round(W * self.scale)), int(round(H * self.scale))
        d = cv2.resize(fill_invalid(f.depth), (w, h), interpolation=cv2.INTER_LINEAR)
        valid = cv2.resize((f.depth > 0).astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST) > 0
        if "valid" in f.extra:
            valid &= cv2.resize(f.extra["valid"].astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST) > 0
        return np.where(valid, d, 0).astype(np.float32), K_for_size(f.K, (W, H), (w, h))


class PromptDADepth:
    name = "promptda"

    def __init__(self, checkpoint: str, size: int, device: str):
        import torch
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation
        self.torch = torch
        self.device = device
        self.proc = AutoImageProcessor.from_pretrained(checkpoint)
        # fp32 on purpose: the model rescales its output to metres internally, and fp16 would
        # quantise depth to ~0.2 mm at 30 cm
        dtype = torch.float32
        self.dtype = dtype
        self.model = AutoModelForDepthEstimation.from_pretrained(checkpoint, torch_dtype=dtype).to(device).eval()
        self.size = int(size)

    def predict(self, f):
        torch = self.torch
        rgb = f.load_rgb()
        H, W = rgb.shape[:2]
        prompt = fill_invalid(f.depth.astype(np.float32))
        rk = int(f.extra.get("upright_k", 0))           # feed the model an upright image
        inputs = self.proc(images=np.ascontiguousarray(np.rot90(rgb, rk)),
                           prompt_depth=np.ascontiguousarray(np.rot90(prompt, rk)), prompt_scale_to_meter=1.0,
                           size={"height": self.size, "width": self.size}, return_tensors="pt")
        inputs = {kk: (v.to(self.device, self.dtype) if v.is_floating_point() else v.to(self.device))
                  for kk, v in inputs.items()}
        with torch.inference_mode():
            out = self.model(**inputs)
        d = np.ascontiguousarray(np.rot90(out.predicted_depth[0].float().cpu().numpy(), -rk))
        h, w = d.shape
        valid = np.ones((h, w), bool)
        if "valid" in f.extra:
            valid = cv2.resize(f.extra["valid"].astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST) > 0
        return np.where(valid, d, 0).astype(np.float32), K_for_size(f.K, (W, H), (w, h))


# --------------------------------------------------------------------------- alignment
def plane_samples(D, Kd, T_wc, n, d0, exclude=None, region=None, step: int = 6):
    """(u, v, z_true) for pixels whose ray hits the plane n.x = d0 (optionally inside region,
    outside the exclusion mask)."""
    h, w = D.shape
    v, u = np.mgrid[step // 2:h:step, step // 2:w:step]
    u, v = u.ravel(), v.ravel()
    ok = D[v, u] > 0
    if exclude is not None:
        ok &= ~exclude[v, u]
    rays = np.stack([(u - Kd[0, 2]) / Kd[0, 0], (v - Kd[1, 2]) / Kd[1, 1], np.ones(len(u))], 1)
    R, C = T_wc[:3, :3], T_wc[:3, 3]
    rw = rays @ R.T
    den = rw @ n
    lam = (d0 - C @ n) / np.where(np.abs(den) < 1e-9, 1e-9, den)
    ok &= (lam > 0.05) & (lam < 1.2)
    if region is not None:
        P = C + rw * lam[:, None]
        xmin, xmax, ymin, ymax = region
        ok &= (P[:, 0] > xmin) & (P[:, 0] < xmax) & (P[:, 1] > ymin) & (P[:, 1] < ymax)
    return u[ok], v[ok], lam[ok]


def track_samples(i, tracks, T_wc, K_rgb, Kd, shape):
    """(u, v, z_true) in depth-map pixels from triangulated points observed in frame i."""
    if tracks is None or tracks.n_points == 0:
        return np.zeros(0, int), np.zeros(0, int), np.zeros(0)
    sel = tracks.obs_cam == i
    if not sel.any():
        return np.zeros(0, int), np.zeros(0, int), np.zeros(0)
    X = tracks.X[tracks.obs_pt[sel]]
    T_cw = inv_T(T_wc)
    Xc = X @ T_cw[:3, :3].T + T_cw[:3, 3]
    uv = tracks.obs_uv[sel]
    # RGB pixel -> depth-map pixel (half-pixel convention)
    sx, sy = Kd[0, 0] / K_rgb[0, 0], Kd[1, 1] / K_rgb[1, 1]
    u = np.round((uv[:, 0] + 0.5) * sx - 0.5).astype(int)
    v = np.round((uv[:, 1] + 0.5) * sy - 0.5).astype(int)
    h, w = shape
    ok = (u >= 0) & (u < w) & (v >= 0) & (v < h) & (Xc[:, 2] > 0.02)
    return u[ok], v[ok], Xc[ok, 2]


def robust_affine(x, y, w=None, delta: float = 0.001, iters: int = 8, scale_sigma: float = 0.02,
                  relief=None, relief_sigma: float = 0.05):
    """Huber IRLS for  y ~ a*x + b (+ c*relief)  with weak priors a ~ 1 +- scale_sigma and c ~ 0 +- relief_sigma.
    The scale prior keeps the fit well posed when all samples sit at nearly the same depth (looking straight
    down at the board). 'relief' = predicted depth minus the support-plane depth along the same ray: the c
    term corrects a depth model that flattens (or exaggerates) the part relative to the table.
    Returns (a, b, c, robust residual sigma)."""
    w = np.ones_like(x) if w is None else w
    r_ = np.zeros_like(x) if relief is None else relief
    use_c = relief is not None
    a, b, c = 1.0, float(np.median(y - x)), 0.0
    sig = delta
    for _ in range(iters):
        r = a * x + b + c * r_ - y
        wr = w * np.where(np.abs(r) <= delta, 1.0, delta / np.maximum(np.abs(r), 1e-12))
        cols = [x, np.ones_like(x)] + ([r_] if use_c else [])
        A = np.stack(cols, 1) * np.sqrt(wr)[:, None] / sig
        rhs = y * np.sqrt(wr) / sig
        pri = [[1.0 / scale_sigma, 0.0] + ([0.0] if use_c else [])]
        prhs = [1.0 / scale_sigma]
        if use_c:
            pri.append([0.0, 0.0, 1.0 / relief_sigma])
            prhs.append(0.0)
        A = np.vstack([A, pri])
        rhs = np.r_[rhs, prhs]
        sol = np.linalg.lstsq(A, rhs, rcond=None)[0]
        a, b = sol[0], sol[1]
        c = sol[2] if use_c else 0.0
    r = a * x + b + c * r_ - y
    return float(a), float(b), float(c), float(1.4826 * np.median(np.abs(r)))


def plane_depth_map(shape, Kd, T_wc, n, d0, max_depth: float = 2.0):
    """Depth at which every pixel's ray meets the support plane n.x = d0 (0 where it does not)."""
    h, w = shape
    v, u = np.mgrid[0:h, 0:w]
    rays = np.stack([(u - Kd[0, 2]) / Kd[0, 0], (v - Kd[1, 2]) / Kd[1, 1], np.ones((h, w))], -1)
    R, C = T_wc[:3, :3], T_wc[:3, 3]
    den = rays @ (R.T @ n)
    lam = (d0 - C @ n) / np.where(np.abs(den) < 1e-9, 1e-9, den)
    return np.where((lam > 0.05) & (lam < max_depth), lam, 0.0).astype(np.float32)


def align_depth(D, Kd, T_wc, K_rgb, i, tracks=None, plane=None, part_mask=None, plane_region=None,
                part_weight: float = 4.0):
    """Correct one dense depth map so it agrees with the photogrammetric geometry."""
    us, vs, zs, ws = [], [], [], []
    if plane is not None:
        excl = None
        if part_mask is not None:
            pm = cv2.resize(part_mask.astype(np.uint8), (D.shape[1], D.shape[0]), interpolation=cv2.INTER_NEAREST)
            excl = cv2.dilate(pm, np.ones((15, 15), np.uint8)) > 0
        u, v, z = plane_samples(D, Kd, T_wc, plane[0], plane[1], excl, plane_region)
        us.append(u), vs.append(v), zs.append(z), ws.append(np.ones(len(z)))
    u, v, z = track_samples(i, tracks, T_wc, K_rgb, Kd, D.shape)
    if len(z):
        near = np.ones(len(z), bool)
        if part_mask is not None:
            pm = cv2.resize(part_mask.astype(np.uint8), (D.shape[1], D.shape[0]), interpolation=cv2.INTER_NEAREST)
            near = cv2.dilate(pm, np.ones((9, 9), np.uint8))[v, u] > 0
        us.append(u), vs.append(v), zs.append(z), ws.append(np.where(near, part_weight, 1.0))
    if not us:
        return D, dict(a=1.0, b=0.0, n=0, sigma_mm=float("nan"), ok=False)
    u, v, z, w = (np.concatenate(x) for x in (us, vs, zs, ws))
    d = D[v, u]
    ok = d > 0
    u, v, z, w, d = u[ok], v[ok], z[ok], w[ok], d[ok]
    if len(z) < 25:
        return D, dict(a=1.0, b=0.0, c=0.0, n=int(len(z)), sigma_mm=float("nan"), ok=False)
    # relief term: only ON the part (inside its outline). Off the part the true depth IS the plane depth,
    # so a relief computed from the prediction there would just echo the prediction's own bias.
    Dp, on_part, relief = None, None, None
    if plane is not None and part_mask is not None:
        Dp = plane_depth_map(D.shape, Kd, T_wc, *plane)
        on_part = cv2.resize(part_mask.astype(np.uint8), (D.shape[1], D.shape[0]),
                             interpolation=cv2.INTER_NEAREST) > 0
        on_part &= Dp > 0
        sel = on_part[v, u]
        relief = np.where(sel, d - Dp[v, u], 0.0)
        if sel.sum() < 15:          # too few points on the part to say anything about its relief
            relief, on_part = None, None
    a, b, c, sig = robust_affine(d, z, w, relief=relief)
    good = 0.85 < a < 1.15 and abs(b) < 0.03 and abs(c) < 0.2
    if not good:
        log.warning("frame %d: depth correction rejected (a=%.3f, b=%.1f mm, c=%.3f)", i, a, b * 1000, c)
        return D, dict(a=a, b=b, c=c, n=int(len(z)), sigma_mm=sig * 1000, ok=False)
    Dc = a * D + b
    if on_part is not None and c != 0.0:
        Dc = Dc + c * np.where(on_part, D - Dp, 0.0)
    Dc = np.where(D > 0, Dc, 0).astype(np.float32)
    return Dc, dict(a=a, b=b, c=c, n=int(len(z)), sigma_mm=sig * 1000, ok=True)
