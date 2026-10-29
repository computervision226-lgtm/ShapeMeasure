"""Finding the part: coarse 3D localisation from LiDAR, then per-frame silhouettes.

Silhouettes matter for accuracy: depth sensors smear depth across object edges, while a
good image mask is sharp to a pixel. The fusion step carves away anything that projects
outside the silhouettes (a robust visual hull), which pins down the outline of the part.
"""
from __future__ import annotations

import logging

import cv2
import numpy as np
from scipy import ndimage

from .geometry import (K_for_size, backproject_depth, closest_point_to_lines, inv_T, project, ransac_plane,
                       transform_points)

log = logging.getLogger("partscan")


# --------------------------------------------------------------------------- coarse 3D
def look_at_point(T_wc: np.ndarray) -> np.ndarray:
    return closest_point_to_lines(T_wc[:, :3, 3], T_wc[:, :3, 2])


def lidar_points(kf, T_wc, stride: int = 2, min_conf: int = 2, max_range: float = 1.5, align=None):
    """World points from the LiDAR maps of all keyframes (optionally per-frame corrected)."""
    pts = []
    for i, f in enumerate(kf):
        if f.depth is None:
            continue
        d = f.depth.copy()
        if align is not None and align[i] is not None:
            a, b = align[i]
            d = np.where(d > 0, a * d + b, 0)
        Kd = K_for_size(f.K, f.rgb_size, (d.shape[1], d.shape[0]))
        m = (f.conf >= min_conf) & (d < max_range) & (d > 0.05)
        P, _ = backproject_depth(d, Kd, T_wc[i], m, stride)
        pts.append(P)
    return np.concatenate(pts) if pts else np.zeros((0, 3))


def plane_lidar_alignment(kf, T_wc, n=np.array([0, 0, 1.0]), d0: float = 0.0, region=None, stride: int = 4):
    """Per-frame scale/offset of the LiDAR depth so that the support plane lands exactly on
    n.x = d0. Uses a trimmed fit (the part itself is an outlier). Returns list of (a, b)."""
    out = []
    for i, f in enumerate(kf):
        if f.depth is None:
            out.append(None)
            continue
        dh, dw = f.depth.shape
        Kd = K_for_size(f.K, f.rgb_size, (dw, dh))
        v, u = np.mgrid[0:dh:stride, 0:dw:stride]
        u, v = u.ravel(), v.ravel()
        dm = f.depth[v, u]
        ok = (f.conf[v, u] >= 1) & (dm > 0.05)
        rays_c = np.stack([(u - Kd[0, 2]) / Kd[0, 0], (v - Kd[1, 2]) / Kd[1, 1], np.ones(len(u))], 1)
        R, C = T_wc[i][:3, :3], T_wc[i][:3, 3]
        rw = rays_c @ R.T
        den = rw @ n
        lam = (d0 - C @ n) / np.where(np.abs(den) < 1e-9, 1e-9, den)   # z-depth since rays_c.z = 1
        P = C + rw * lam[:, None]
        ok &= (lam > 0.05) & (lam < 1.5)
        if region is not None:
            xmin, xmax, ymin, ymax = region
            ok &= (P[:, 0] > xmin) & (P[:, 0] < xmax) & (P[:, 1] > ymin) & (P[:, 1] < ymax)
        if ok.sum() < 30:
            out.append(None)
            continue
        x, y = dm[ok], lam[ok]
        a, b = 1.0, 0.0
        keep = np.ones(len(x), bool)
        for _ in range(4):
            A = np.stack([x[keep], np.ones(keep.sum())], 1)
            a, b = np.linalg.lstsq(A, y[keep], rcond=None)[0]
            res = np.abs(a * x + b - y)
            keep = res <= np.quantile(res, 0.7) + 1e-4
        out.append((float(a), float(b)) if 0.8 < a < 1.2 and abs(b) < 0.05 else None)
    return out


def coarse_part(kf, T_wc, world: str, board_extent=None, align=None):
    """Locate the part. Returns dict with T_pw (part-from-world), roi_min/max in part frame,
    coarse part points (part frame) and the look-at prior."""
    look = look_at_point(T_wc)
    pts_w = lidar_points(kf, T_wc, align=align)
    if world == "board":
        T_pw = np.eye(4)
    else:
        near = pts_w[np.linalg.norm(pts_w - look, axis=1) < 0.3]
        if len(near) < 100:
            raise RuntimeError("Not enough LiDAR points near the scanned area. Was the object at least 25 cm away?")
        n, d, inl = ransac_plane(near, thresh=0.004, iters=600, normal_hint=np.array([0, 1.0, 0]), max_angle_deg=25)
        z = n
        x = np.cross([0, 0, 1.0], z)
        if np.linalg.norm(x) < 1e-6:
            x = np.cross([1.0, 0, 0], z)
        x /= np.linalg.norm(x)
        y = np.cross(z, x)
        o = look - (look @ n - d) * n
        T_wp = np.eye(4)
        T_wp[:3, :3] = np.stack([x, y, z], 1)
        T_wp[:3, 3] = o
        T_pw = inv_T(T_wp)
    P = transform_points(T_pw, pts_w)
    lk = transform_points(T_pw, look[None])[0]
    # height threshold from the spread of the plane points
    ring = P[np.hypot(P[:, 0] - lk[0], P[:, 1] - lk[1]) < 0.2]
    zz = ring[:, 2]
    plane_z = zz[np.abs(zz) < 0.01]
    sig = 1.4826 * np.median(np.abs(plane_z - np.median(plane_z))) if len(plane_z) > 50 else 0.003
    zmin = float(np.clip(4 * sig, 0.0025, 0.008))
    cand = P[(P[:, 2] > zmin) & (P[:, 2] < 0.35) & (np.hypot(P[:, 0] - lk[0], P[:, 1] - lk[1]) < 0.25)]
    if board_extent is not None:
        xmin, xmax, ymin, ymax = board_extent
        m = 0.08   # parts may overhang the board
        cand = cand[(cand[:, 0] > xmin - m) & (cand[:, 0] < xmax + m) & (cand[:, 1] > ymin - m) & (cand[:, 1] < ymax + m)]
    info = dict(T_pw=T_pw, look_at=lk, plane_sigma=sig, z_threshold=zmin)
    if len(cand) < 30:
        log.warning("LiDAR barely sees the part; using a default search box around the scan centre")
        c = lk.copy()
        c[2] = 0
        info.update(roi_min=c + [-0.07, -0.07, -0.003], roi_max=c + [0.07, 0.07, 0.12], points=np.zeros((0, 3)))
        return info
    vox = 0.003
    lo = cand.min(0)
    idx = np.floor((cand - lo) / vox).astype(int)
    dims = idx.max(0) + 1
    occ = np.zeros(dims, np.int32)
    np.add.at(occ, tuple(idx.T), 1)
    lab, nlab = ndimage.label(occ >= 2, structure=np.ones((3, 3, 3)))
    if nlab == 0:
        lab, nlab = ndimage.label(occ >= 1, structure=np.ones((3, 3, 3)))
    pl = lab[tuple(idx.T)]
    best, best_s = None, -1
    for k in range(1, nlab + 1):
        sel = pl == k
        cnt = sel.sum()
        if cnt < 10:
            continue
        cxy = cand[sel, :2].mean(0)
        s = cnt / (1 + (np.linalg.norm(cxy - lk[:2]) / 0.05) ** 2)
        if s > best_s:
            best, best_s = sel, s
    if best is None:
        best = np.ones(len(cand), bool)
    part = cand[best]
    lo_p, hi_p = np.percentile(part, 0.5, 0), np.percentile(part, 99.5, 0)
    size = hi_p - lo_p
    margin = np.maximum(0.012, 0.25 * size)
    margin[2] = max(0.012, 0.25 * size[2])
    info.update(roi_min=np.array([lo_p[0] - margin[0], lo_p[1] - margin[1], -0.003]),
                roi_max=np.array([hi_p[0] + margin[0], hi_p[1] + margin[1], hi_p[2] + margin[2]]),
                points=part)
    return info


# --------------------------------------------------------------------------- masks helpers
def splat_mask(points_w, K, T_cw, size_wh, radius_m: float = 0.002):
    """Render points as discs -> coarse silhouette."""
    W, H = size_wh
    m = np.zeros((H, W), np.uint8)
    if len(points_w) == 0:
        return m
    uv, z = project(K, T_cw, points_w)
    ok = (z > 0.02) & (uv[:, 0] >= -50) & (uv[:, 0] < W + 50) & (uv[:, 1] >= -50) & (uv[:, 1] < H + 50)
    uv, z = uv[ok], z[ok]
    rad = np.clip(radius_m * K[0, 0] / z, 1, 60).astype(int)
    for (u, v), r in zip(uv.astype(int), rad):
        cv2.circle(m, (int(u), int(v)), int(r), 1, -1)
    return m


def mesh_silhouette(V, F, K, T_cw, size_wh):
    """Binary silhouette of a mesh (world vertices V) in an image."""
    W, H = size_wh
    Xc = V @ T_cw[:3, :3].T + T_cw[:3, 3]
    z = np.maximum(Xc[:, 2], 1e-4)
    uv = Xc[:, :2] / z[:, None] * [K[0, 0], K[1, 1]] + K[:2, 2]
    sil = np.zeros((H, W), np.uint8)
    tri = np.round(uv[F] * 8).astype(np.int64)
    ok = (Xc[F][:, :, 2] > 1e-3).all(1)
    tri = np.clip(tri[ok], -(2 ** 28), 2 ** 28).astype(np.int32)
    from .geometry import fill_triangles
    fill_triangles(sil, tri, 1, shift=3)
    return sil > 0


def mask_sdt(mask: np.ndarray) -> np.ndarray:
    """Signed distance (pixels) to the mask boundary: >0 outside, <0 inside."""
    m = mask.astype(np.uint8)
    out = cv2.distanceTransform(1 - m, cv2.DIST_L2, 5)
    ins = cv2.distanceTransform(m, cv2.DIST_L2, 5)
    return (out - ins).astype(np.float32)


def guided_filter(I: np.ndarray, p: np.ndarray, r: int, eps: float) -> np.ndarray:
    ksz = (2 * r + 1, 2 * r + 1)
    mean = lambda x: cv2.boxFilter(x, cv2.CV_32F, ksz)
    mI, mp = mean(I), mean(p)
    a = (mean(I * p) - mI * mp) / (mean(I * I) - mI * mI + eps)
    b = mp - a * mI
    return mean(a) * I + mean(b)


# --------------------------------------------------------------------------- segmenters
class GeometrySegmenter:
    """No AI: silhouette = projection of the coarse 3D points (blurry; not used for carving)."""
    name = "geometry"
    trusted = False

    def segment(self, f, coarse_mask, box, pos, neg):
        return coarse_mask.astype(bool), 0.5


class Sam2Segmenter:
    """SAM 2.1 (Apache-2.0) prompted with a box + points that come from the 3D localisation."""
    name = "sam2"
    trusted = True

    def __init__(self, checkpoint: str, device: str):
        import torch
        from transformers import Sam2Model, Sam2Processor
        self.torch = torch
        self.device = device
        dtype = torch.float32
        self.model = Sam2Model.from_pretrained(checkpoint, torch_dtype=dtype).to(device).eval()
        self.proc = Sam2Processor.from_pretrained(checkpoint)

    def segment(self, f, coarse_mask, box, pos, neg):
        torch = self.torch
        rgb0 = f.load_rgb()
        H0, W0 = rgb0.shape[:2]
        rk = int(f.extra.get("upright_k", 0))           # SAM sees an upright image
        rgb = np.ascontiguousarray(np.rot90(rgb0, rk))
        from .geometry import rot_points
        pos = rot_points(pos, rk, W0, H0)
        neg = rot_points(neg, rk, W0, H0)
        bx = rot_points([(box[0], box[1]), (box[2], box[3])], rk, W0, H0)
        box = [min(bx[0][0], bx[1][0]), min(bx[0][1], bx[1][1]), max(bx[0][0], bx[1][0]), max(bx[0][1], bx[1][1])]
        coarse_mask = np.rot90(coarse_mask, rk)
        pts = [list(map(float, p)) for p in pos] + [list(map(float, p)) for p in neg]
        lbl = [1] * len(pos) + [0] * len(neg)
        kw = dict(images=rgb, input_boxes=[[list(map(float, box))]], return_tensors="pt")
        if pts:
            kw.update(input_points=[[pts]], input_labels=[[lbl]])
        inputs = self.proc(**kw).to(self.device)
        with torch.inference_mode():
            out = self.model(**inputs, multimask_output=True)
        logits = self.proc.post_process_masks(out.pred_masks.float().cpu(), inputs["original_sizes"].cpu(),
                                              binarize=False)[0][0]           # (3,H,W) logits
        scores = out.iou_scores[0, 0].float().cpu().numpy()
        ref = coarse_mask.astype(bool)
        best, best_s = None, -1
        for k in range(logits.shape[0]):
            m = logits[k].numpy() > 0
            inter = (m & ref).sum()
            union = (m | ref).sum() + 1
            s = 0.6 * inter / union + 0.4 * float(scores[k])
            if s > best_s:
                best, best_s = k, s
        prob = 1 / (1 + np.exp(-np.clip(logits[best].numpy(), -30, 30)))
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
        r = max(2, int(round(rgb.shape[1] / 480)))
        refined = guided_filter(gray, prob.astype(np.float32), r, 1e-3) > 0.5
        # keep the component(s) overlapping the coarse prior
        n, lab = cv2.connectedComponents(refined.astype(np.uint8))
        if n > 2:
            keep = np.zeros(n, bool)
            for k in range(1, n):
                if (ref & (lab == k)).sum() > 0.02 * max(1, ref.sum()) or (lab == k).sum() > 0.2 * refined.sum():
                    keep[k] = True
            refined = keep[lab]
        return np.ascontiguousarray(np.rot90(refined, -rk)), float(best_s)


def make_prompts(f, T_wc, part_pts, look_at, coarse_radius=0.002):
    """Coarse mask + box + positive/negative points for one frame."""
    T_cw = inv_T(T_wc)
    W, H = f.rgb_size
    cm = splat_mask(part_pts, f.K, T_cw, (W, H), coarse_radius)
    cm = cv2.morphologyEx(cm, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    ys, xs = np.nonzero(cm)
    if len(xs) == 0:
        return None
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    pad = 0.08 * max(x1 - x0, y1 - y0) + 4
    box = [max(0, x0 - pad), max(0, y0 - pad), min(W - 1, x1 + pad), min(H - 1, y1 + pad)]
    dt = cv2.distanceTransform(cm, cv2.DIST_L2, 5)
    pos = []
    for _ in range(2):
        v, u = np.unravel_index(np.argmax(dt), dt.shape)
        if dt[v, u] < 3:
            break
        pos.append((u, v))
        cv2.circle(dt, (int(u), int(v)), int(max(6, dt[v, u])), 0, -1)
    # negatives: a ring on the support plane around the part
    if len(part_pts):
        c = part_pts[:, :2].mean(0)
        rad = np.percentile(np.linalg.norm(part_pts[:, :2] - c, axis=1), 98) + 0.02
    else:
        c, rad = look_at[:2], 0.08
    ang = np.linspace(0, 2 * np.pi, 8, endpoint=False)
    ring = np.stack([c[0] + rad * np.cos(ang), c[1] + rad * np.sin(ang), np.zeros(8)], 1)
    uv, z = project(f.K, T_cw, ring)
    neg = [(u, v) for (u, v), zz in zip(uv, z) if zz > 0 and 0 <= u < W and 0 <= v < H and not cm[int(v), int(u)]]
    if len(neg) > 4:   # spread the negative clicks around the part instead of clustering on one side
        neg = [neg[int(round(i))] for i in np.linspace(0, len(neg) - 1, 4)]
    return dict(coarse=cm, box=box, pos=pos, neg=neg)
