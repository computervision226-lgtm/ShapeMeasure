"""End-to-end pipeline: capture -> poses -> silhouettes -> depth -> fusion -> mesh -> measurements."""
from __future__ import annotations

import gc
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import depth as depthmod
from . import segment as segmod
from .board import board_extent_part
from .config import Config
from .fusion import FusionFrame, auto_voxel, extract_mesh, fuse, sample_sdf
from .geometry import K_for_size, backproject_depth, inv_T, ransac_plane, transform_points
from .io_scans import load_scan, select_keyframes
from .measure import measure_all
from .meshproc import canonical_frame, clean_mesh, colorize
from .poses import feature_refine, init_poses, refine_with_board, undistort_keyframes

RUN_LOCK = threading.Lock()      # one reconstruction at a time: the GPU (and RAM) cannot take two

log = logging.getLogger("partscan")

STAGES = [("load", 0.10), ("poses", 0.14), ("photogrammetry", 0.16), ("silhouettes", 0.14),
          ("depth", 0.16), ("fusion", 0.14), ("mesh", 0.08), ("export", 0.08)]


class Progress:
    def __init__(self, cb=None):
        self.cb = cb
        self.base = 0.0
        self.span = 0.0
        self.t0 = time.time()

    def stage(self, name):
        acc = 0.0
        for n, w in STAGES:
            if n == name:
                self.base, self.span = acc, w
                break
            acc += w
        self(0.0, name)

    def __call__(self, frac, msg=""):
        if self.cb is not None:
            self.cb(min(0.999, self.base + self.span * float(frac)), msg)

    def sub(self, lo, hi):
        return lambda p, m="": self(lo + (hi - lo) * p, m)


@dataclass
class Result:
    out_dir: str
    files: dict = field(default_factory=dict)
    measurements: dict = field(default_factory=dict)
    quality: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    seconds: float = 0.0


def resolve_device(dev: str) -> str:
    if dev != "auto":
        return dev
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


_MODEL_CACHE = {}


def get_depth_model(cfg: Config, device: str):
    if cfg.depth_model == "lidar":
        return depthmod.LidarUpsampler()
    key = ("promptda", cfg.promptda_checkpoint, device)
    if key not in _MODEL_CACHE:
        _MODEL_CACHE[key] = depthmod.PromptDADepth(cfg.promptda_checkpoint, cfg.promptda_size, device)
    _MODEL_CACHE[key].size = int(cfg.promptda_size)      # one model, any processing size
    return _MODEL_CACHE[key]


def get_segmenter(cfg: Config, device: str):
    if cfg.segmenter == "geometry":
        return segmod.GeometrySegmenter()
    key = ("sam2", cfg.sam2_checkpoint, device)
    if key not in _MODEL_CACHE:
        _MODEL_CACHE[key] = segmod.Sam2Segmenter(cfg.sam2_checkpoint, device)
    return _MODEL_CACHE[key]


def _depth_edges(D, rel=0.012):
    """Pixels on/next to depth discontinuities (mixed 'flying' pixels): the depth range inside a
    3x3 window exceeds rel*depth. Smooth surfaces stay in up to ~80 deg grazing."""
    valid = D > 0
    big = np.where(valid, D, 0).astype(np.float32)
    small = np.where(valid, D, 1e3).astype(np.float32)
    k = np.ones((3, 3), np.uint8)
    rng_ = cv2.dilate(big, k) - cv2.erode(small, k)
    e = (rng_ > rel * np.where(valid, D, 1)) | ~valid
    return cv2.dilate(e.astype(np.uint8), k) > 0


def _occluding_contours(D, mask, band_px, thr=0.003):
    """Band around the silhouette where the part's depth differs from the background's (an
    occluding contour). Contact lines (part meets the table) are NOT flagged."""
    m = mask.astype(np.float32)
    r = 2 * band_px + 1
    Dv = np.where(D > 0, D, 0).astype(np.float32)
    w_in = cv2.blur(m * (D > 0), (r, r))
    w_out = cv2.blur((1 - m) * (D > 0), (r, r))
    d_in = cv2.blur(Dv * m, (r, r)) / np.maximum(w_in, 1e-6)
    d_out = cv2.blur(Dv * (1 - m), (r, r)) / np.maximum(w_out, 1e-6)
    near = (w_in > 0.05) & (w_out > 0.05)
    return near & (np.abs(d_out - d_in) > thr)


def _edge_free_depth(D, keep, max_step: int = 10, gap: int = 3, margin: float = 0.0002):
    """For pixels excluded at depth edges: a safe 'nothing is closer than this' depth.
    Walk from the pixel towards the near side of the edge (minus the depth gradient) to the first
    clean pixel, read a second clean pixel `gap` further, and extrapolate that near surface back
    to the pixel. Whichever side of the edge the pixel's ray really hits, everything in front of
    this depth is empty."""
    h, w = D.shape
    Ds = cv2.GaussianBlur(np.where(D > 0, D, 0).astype(np.float32), (5, 5), 1.0)
    gx = cv2.Sobel(Ds, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(Ds, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.hypot(gx, gy) + 1e-12
    vv, uu = np.nonzero(~keep & (D > 0))
    free = np.zeros((h, w), np.float32)
    if len(uu) == 0:
        return free
    dx, dy = -gx[vv, uu] / mag[vv, uu], -gy[vv, uu] / mag[vv, uu]
    s1 = np.zeros(len(uu), np.int32)
    for s in range(1, max_step + 1):          # first clean pixel along the direction
        u1 = np.clip(np.round(uu + s * dx).astype(int), 0, w - 1)
        v1 = np.clip(np.round(vv + s * dy).astype(int), 0, h - 1)
        hit = (s1 == 0) & keep[v1, u1] & (D[v1, u1] > 0)
        s1[hit] = s
    found = s1 > 0
    s1f = np.where(found, s1, 1).astype(np.float32)
    u1 = np.clip(np.round(uu + s1f * dx).astype(int), 0, w - 1)
    v1 = np.clip(np.round(vv + s1f * dy).astype(int), 0, h - 1)
    u2 = np.clip(np.round(uu + (s1f + gap) * dx).astype(int), 0, w - 1)
    v2 = np.clip(np.round(vv + (s1f + gap) * dy).astype(int), 0, h - 1)
    d1, d2 = D[v1, u1], D[v2, u2]
    ok = found & keep[v2, u2] & (d2 > 0)
    # the actual step lengths after rounding
    l1 = np.hypot(u1 - uu, v1 - vv)
    l2 = np.hypot(u2 - uu, v2 - vv)
    slope = (d2 - d1) / np.maximum(l2 - l1, 1e-6)
    dfg = d1 - slope * l1
    dmin = cv2.erode(np.where(D > 0, D, 1e3).astype(np.float32), np.ones((5, 5), np.uint8))[vv, uu]
    dfg = np.where(ok, dfg, np.where(found, np.minimum(d1, D[vv, uu]), dmin))
    dfg = np.minimum(dfg, D[vv, uu]) - margin
    free[vv, uu] = np.where(dfg > 0.05, dfg, 0)
    return free


def _sdt(mask, f):
    """Silhouette signed distance; pixels without image data (undistortion border) never vote 'outside'."""
    sdt = segmod.mask_sdt(mask)
    if "valid" in f.extra:
        sdt[~f.extra["valid"]] = -1000.0
    return sdt


def _plane_pixels(D, Kd, T_wc, tol=0.0025):
    """Pixels whose depth puts them on the support plane z = 0 (part frame)."""
    h, w = D.shape
    v, u = np.mgrid[0:h, 0:w]
    z = D
    X = np.stack([(u - Kd[0, 2]) / Kd[0, 0] * z, (v - Kd[1, 2]) / Kd[1, 1] * z, z], -1)
    zw = X @ T_wc[2, :3] + T_wc[2, 3]
    return (D > 0) & (np.abs(zw) < tol)


def _angle_weight(D, Kd):
    """cos^2 of the angle between viewing ray and surface (from depth gradients)."""
    z = np.where(D > 0, D, np.nan).astype(np.float32)
    gx = cv2.Sobel(np.nan_to_num(z), cv2.CV_32F, 1, 0, ksize=3) / 8.0
    gy = cv2.Sobel(np.nan_to_num(z), cv2.CV_32F, 0, 1, ksize=3) / 8.0
    sx = gx * Kd[0, 0] / np.where(D > 0, D, 1)
    sy = gy * Kd[1, 1] / np.where(D > 0, D, 1)
    c2 = 1.0 / (1.0 + sx * sx + sy * sy)
    return np.clip(c2, 0.03, 1.0).astype(np.float32)


def run(scan_path: str, out_dir: str, cfg: Config, progress=None, depth_model=None, segmenter=None,
        hooks=None) -> Result:
    """Run everything. depth_model / segmenter may be injected (tests)."""
    t_start = time.time()
    os.makedirs(out_dir, exist_ok=True)
    work = os.path.join(out_dir, "_work")
    os.makedirs(work, exist_ok=True)
    P = Progress(progress)
    res = Result(out_dir)
    q = res.quality
    device = resolve_device(cfg.device)
    q["device"] = device
    hooks = hooks or {}

    # ------------------------------------------------------------------ 1. load
    P.stage("load")
    scan = load_scan(scan_path, work, cfg.max_candidates, progress=P.sub(0, 0.8))
    res.warnings += scan.notes
    kf = select_keyframes(scan.frames, cfg.n_keyframes, cfg.min_sharpness_ratio)
    if len(kf) < 12:
        raise RuntimeError(f"Only {len(kf)} usable frames. Record a longer, slower scan (30-60 s) around the part.")
    for f in kf:
        f.load_rgb()
    if not cfg.debug:
        import shutil
        shutil.rmtree(os.path.join(work, "extracted"), ignore_errors=True)   # everything needed is in memory
    keep_ids = {id(f) for f in kf}
    for f in scan.frames:
        if id(f) not in keep_ids:
            f.rgb_bytes = None
    gc.collect()
    q.update(source=scan.source, frames_total=scan.n_total, keyframes=len(kf), rgb_size=list(kf[0].rgb_size))
    log.info("%s: %d frames, %d keyframes, %dx%d", scan.source, scan.n_total, len(kf), *kf[0].rgb_size)

    # ------------------------------------------------------------------ 2. poses
    P.stage("poses")
    st = init_poses(kf, cfg, progress=P.sub(0, 0.6))
    board_ext = None
    if st.world == "board":
        st = refine_with_board(kf, st, cfg)
        undistort_keyframes(kf, st)
        board_ext = board_extent_part(cfg.board)
        lidar_align = segmod.plane_lidar_alignment(kf, st.T_wc, region=board_ext)
        q["scale_reference"] = "ChArUco board"
    else:
        lidar_align = None
        q["scale_reference"] = "iPhone LiDAR/ARKit (no board found)"
        res.warnings.append("No calibration board detected: absolute scale comes from the phone's tracking "
                            "(typically 0.5-2% error). Use the printed board or enter a caliper reference.")
    q.update({k: v for k, v in st.stats.items()})
    if cfg.use_board and st.world != "board" and st.stats.get("board_frames", 0) > 0:
        res.warnings.append(f"The board was recognised in only {st.stats['board_frames']} of {len(kf)} keyframes, so "
                            "it was not used. Keep the whole board in view and avoid glare on the paper.")
    P(0.8, "locating the part")
    info = segmod.coarse_part(kf, st.T_wc, st.world, board_ext, lidar_align)
    if st.world != "board":
        st.T_wc = np.array([info["T_pw"] @ T for T in st.T_wc])
    roi_min, roi_max = info["roi_min"], info["roi_max"]
    if "roi" in hooks:
        hooks["roi"](info)

    # ------------------------------------------------------------------ 3. photogrammetric refinement
    P.stage("photogrammetry")
    if cfg.feature_ba:
        st = feature_refine(kf, st, roi_min, roi_max, cfg, progress=P.sub(0, 0.95))
        q.update({k: v for k, v in st.stats.items() if k.startswith(("feature", "ba_", "poses_"))})
        if st.tracks is None and st.world != "board":
            res.warnings.append("Very few image features matched between frames, so the phone's own tracking was "
                                "used without photogrammetric refinement. More light, slower motion or the board help.")
    plane = (np.array([0, 0, 1.0]), 0.0)
    if st.world != "board" and st.tracks is not None and st.tracks.n_points > 100:
        # re-estimate the support plane from triangulated points (more precise than LiDAR), ignoring
        # everything over the part's footprint (a flat part's top must not be taken for the table)
        X = st.tracks.X
        cand = (np.abs(X[:, 2]) < 0.006) & (np.hypot(X[:, 0], X[:, 1]) < 0.25)
        fp_lo, fp_hi = info["roi_min"][:2] - 0.005, info["roi_max"][:2] + 0.005
        cand &= ~np.all((X[:, :2] > fp_lo) & (X[:, :2] < fp_hi), 1)
        cand = X[cand]
        n = None
        if len(cand) > 50:
            n, d, inl = ransac_plane(cand, 0.0008, 500, normal_hint=np.array([0, 0, 1.0]), max_angle_deg=10)
            if np.degrees(np.arccos(min(1.0, n[2]))) > 3.0 or abs(d) > 0.004:
                log.warning("photogrammetric table plane disagrees with LiDAR (%.1f deg, %.1f mm) - keeping LiDAR's",
                            np.degrees(np.arccos(min(1.0, n[2]))), d * 1000)
                n = None
        if n is not None:
            T_fix = np.eye(4)
            zax = n
            xax = np.cross([0, 1.0, 0], zax)
            xax /= np.linalg.norm(xax)
            yax = np.cross(zax, xax)
            T_fix[:3, :3] = np.stack([xax, yax, zax], 0)
            T_fix[:3, 3] = -T_fix[:3, :3] @ (n * d)
            st.T_wc = np.array([T_fix @ T for T in st.T_wc])
            st.tracks.X = transform_points(T_fix, X)
            if len(info["points"]):
                info["points"] = transform_points(T_fix, info["points"])
            info["look_at"] = transform_points(T_fix, info["look_at"][None])[0]
            q["support_plane_points"] = int(inl.sum())
    if "poses" in hooks:
        hooks["poses"](kf, st)
    if len(info["points"]) < 30:
        # LiDAR barely saw the part (small or flat): seed the silhouette prompts from tie points above
        # the support plane, or from a small disc where the cameras were pointing
        X = st.tracks.X if st.tracks is not None else np.zeros((0, 3))
        up = X[(X[:, 2] > 0.0008) & (X[:, 2] < 0.3) & np.all((X >= roi_min) & (X <= roi_max), 1)] if len(X) else X
        if len(up) >= 20:
            info["points"] = up
        else:
            c = info["look_at"]
            a = np.linspace(0, 2 * np.pi, 24, endpoint=False)
            info["points"] = np.concatenate([np.stack([c[0] + r * np.cos(a), c[1] + r * np.sin(a), np.full(24, z)], 1)
                                             for r in (0.004, 0.009) for z in (0.001, 0.004)])
        res.warnings.append("The LiDAR barely saw this part (it is small or flat); its outline was found from the "
                            "photos instead. Check the silhouette panel.")

    # ------------------------------------------------------------------ 4. silhouettes
    P.stage("silhouettes")
    from .geometry import upright_k
    for i, f in enumerate(kf):   # gravity-up direction in each image (the AI models prefer upright images)
        f.extra["upright_k"] = upright_k(inv_T(st.T_wc[i])[:3, :3] @ np.array([0, 0, 1.0]))
    seg = segmenter or get_segmenter(cfg, device)
    masks, scores = [], []
    for i, f in enumerate(kf):
        pr = segmod.make_prompts(f, st.T_wc[i], info["points"], info["look_at"])
        if pr is None:
            masks.append(None)
            scores.append(0.0)
            continue
        m, s = seg.segment(f, pr["coarse"], pr["box"], pr["pos"], pr["neg"])
        masks.append(m)
        scores.append(s)
        P(0.9 * i / len(kf), f"segmenting {i + 1}/{len(kf)}")
    trusted = getattr(seg, "trusted", False)
    q["segmenter"] = getattr(seg, "name", "custom")
    # quick silhouette-only hull to tighten the ROI and re-check the masks
    if trusted:
        hull_frames = (FusionFrame(inv_T(st.T_wc[i]), None, None, None, _sdt(m, kf[i]).astype(np.float16), kf[i].K)
                       for i, m in enumerate(masks) if m is not None)      # generator: one SDT at a time
        v_c = float(np.clip(np.max(roi_max - roi_min) / 70.0, 0.0005, 0.0015))   # coarse hull, scaled to the part
        hvol, _ = fuse(hull_frames, roi_min, roi_max, v_c, trunc=3 * v_c, k_carve=cfg.carve_min_views,
                       tol_px=2.0, close_bottom=True, device=device)
        del hull_frames
        solid = hvol < 0
        del hvol
        gc.collect()
        if solid.sum() > 20:
            idx = np.argwhere(solid)
            lo = roi_min + idx.min(0) * v_c
            hi = roi_min + (idx.max(0) + 1) * v_c
            pad = np.array([0.004, 0.004, 0.004])
            roi_min = np.maximum(roi_min, lo - pad)
            roi_max = np.minimum(roi_max, hi + pad)
            roi_min[2] = -0.002
            # IoU of every mask with the hull projection: flag silhouettes that disagree
            pts = (np.argwhere(solid)[:: max(1, int(solid.sum() // 20000))] + 0.5) * v_c + info["roi_min"]
            for i, m in enumerate(masks):
                if m is None:
                    continue
                hm = segmod.splat_mask(pts, kf[i].K, inv_T(st.T_wc[i]), kf[i].rgb_size, 0.9 * v_c)
                hm = cv2.morphologyEx(hm, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)) > 0
                iou = (hm & m).sum() / max(1, (hm | m).sum())
                scores[i] = float(iou)
        bad = [i for i, s in enumerate(scores) if masks[i] is not None and s < 0.7]
        for i in bad:
            masks[i] = None
        q["silhouettes_rejected"] = len(bad)
        if len(bad) > 0.2 * len(kf):
            res.warnings.append(f"{len(bad)} of {len(kf)} part outlines disagreed with the others and were ignored "
                                "(busy background, part touching other objects, or poor light?).")
        q["silhouette_iou_median"] = float(np.median([s for s in scores if s > 0])) if any(scores) else float("nan")
    q["roi_mm"] = [[float(v * 1000) for v in roi_min], [float(v * 1000) for v in roi_max]]
    if "masks" in hooks:
        hooks["masks"](kf, masks, st)

    # ------------------------------------------------------------------ 5. depth
    if "depth_inputs" in hooks:
        hooks["depth_inputs"](kf, st, masks, info)
    P.stage("depth")
    dm = depth_model or get_depth_model(cfg, device)
    q["depth_model"] = getattr(dm, "name", "custom")
    region = board_ext if st.world == "board" else (-0.15, 0.15, -0.15, 0.15)
    depths, align_stats = [], []
    for i, f in enumerate(kf):
        D, Kd = dm.predict(f)
        if cfg.align_depth:
            pm = masks[i]
            if pm is None:
                pr = segmod.make_prompts(f, st.T_wc[i], info["points"], info["look_at"])
                pm = None if pr is None else cv2.dilate(pr["coarse"], np.ones((25, 25), np.uint8)) > 0
            D, a = depthmod.align_depth(D, Kd, st.T_wc[i], f.K, i, st.tracks, plane, pm, region)
        else:
            a = dict(a=1.0, b=0.0, n=0, sigma_mm=float("nan"), ok=False)
        depths.append((D, Kd))
        align_stats.append(a)
        P(i / len(kf), f"depth {i + 1}/{len(kf)}")
    ok = [a for a in align_stats if a["ok"]]
    if ok:
        q["depth_scale_median"] = float(np.median([a["a"] for a in ok]))
        q["depth_offset_median_mm"] = float(np.median([a["b"] for a in ok]) * 1000)
        q["depth_fit_sigma_mm"] = float(np.median([a["sigma_mm"] for a in ok]))
        q["depth_relief_correction_median"] = float(np.median([a.get("c", 0.0) for a in ok]))
    q["depth_frames_corrected"] = len(ok)
    if cfg.align_depth and len(ok) < 0.8 * len(kf):
        res.warnings.append(f"Only {len(ok)} of {len(kf)} depth maps could be checked against the photogrammetry; "
                            "the others count less in the 3D model.")

    # ------------------------------------------------------------------ 6. fusion
    P.stage("fusion")
    voxel = auto_voxel(roi_min, roi_max, cfg.voxel_mm)
    def fusion_frames(keep_masks):
        """Build each frame's fusion inputs on demand (memory: only one frame's buffers at a time)."""
        for i, f in enumerate(kf):
            D, Kd = depths[i]
            h, w = D.shape
            # part AND background are fused (background = evidence of free space); skip mixed pixels
            mk = ~_depth_edges(D)
            mr = None
            if masks[i] is not None:
                mr = cv2.resize(masks[i].astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST)
                mk &= ~_occluding_contours(D, mr, max(2, int(round(4 * w / 1920))))
            sdt = _sdt(masks[i], f).astype(np.float16) if (trusted and masks[i] is not None) else None
            # excluded pixels still prove that space in front of the near surface is empty
            free = _edge_free_depth(D, mk)
            # support-plane pixels away from the part: the plane is known exactly, so they only count as
            # free space (otherwise depth noise on the table becomes small solid blobs / a skirt)
            on_plane = _plane_pixels(D, Kd, st.T_wc[i], tol=0.0025)
            if mr is not None:
                on_plane &= cv2.dilate(mr, np.ones((7, 7), np.uint8)) == 0
            free = np.where(on_plane, np.maximum(D - 0.0003, 0), free).astype(np.float32)
            mk &= ~on_plane
            keep_masks.append(mk)
            # frames whose depth could not be checked against the photogrammetry count less
            fw = 1.0 if (not cfg.align_depth or align_stats[i]["ok"]) else 0.25
            yield FusionFrame(inv_T(st.T_wc[i]), D, Kd, mk, sdt, f.K, weight=fw,
                              weight_map=_angle_weight(D, Kd).astype(np.float16), free_depth=free)

    depth_masks = []
    fit_sig = q.get("depth_fit_sigma_mm", 0.5) / 1000.0
    trunc = float(np.clip(max(cfg.trunc_voxels * voxel, 3.0 * fit_sig), 0.001, 0.004))
    q["fusion_trunc_mm"] = trunc * 1000
    vol, fst = fuse(fusion_frames(depth_masks), roi_min, roi_max, voxel, trunc, cfg.carve_min_views,
                    cfg.carve_tolerance_px, cfg.close_bottom, device, progress=P.sub(0, 1),
                    min_views=cfg.fusion_min_views, trunc_back=max(3 * voxel, cfg.trunc_back_ratio * trunc))
    gc.collect()
    if device.startswith("cuda"):
        import torch
        torch.cuda.empty_cache()
    q.update({f"fusion_{k}": v for k, v in fst.items()})
    edge_solid = [(vol[s] < 0).mean() for s in ((0,), (-1,), (slice(None), 0), (slice(None), -1),
                                                (slice(None), slice(None), -1))]
    if max(edge_solid) > 0.002:
        res.warnings.append("The model touches the edge of the reconstruction box - part of it may be cut off. "
                            "Is the part much larger than the board, or not on the board?")

    # ------------------------------------------------------------------ 7. mesh + measurements
    P.stage("mesh")
    mesh = extract_mesh(vol, roi_min, voxel)
    if cfg.debug:
        mesh.export(os.path.join(work, "raw_marching_cubes.ply"))
        np.save(os.path.join(work, "sdf_volume.npy"), vol)
    # consistency of the depth data with the final surface
    errs = []
    for i in range(0, len(kf), max(1, len(kf) // 15)):
        D, Kd = depths[i]
        sel = depth_masks[i]
        if masks[i] is not None:
            sel = sel & (cv2.resize(masks[i].astype(np.uint8), (D.shape[1], D.shape[0]), interpolation=cv2.INTER_NEAREST) > 0)
        Pw, _ = backproject_depth(D, Kd, st.T_wc[i], sel, stride=3)
        if len(Pw):
            s = sample_sdf(vol, roi_min, voxel, Pw)
            errs.append(s[np.isfinite(s) & (np.abs(s) < 0.9 * trunc)])
    if errs:
        e = np.concatenate(errs)
        q["depth_to_surface_rms_mm"] = float(np.sqrt(np.mean(e ** 2)) * 1000) if len(e) else float("nan")
    mesh = clean_mesh(mesh, cfg.smooth_iterations, cfg.max_faces)
    ious = []
    try:
        m_small = mesh.simplify_quadric_decimation(face_count=40000) if len(mesh.faces) > 60000 else mesh
    except Exception:
        m_small = mesh
    ratios = []
    for i in range(len(kf)):
        if masks[i] is None:
            continue
        sil = segmod.mesh_silhouette(m_small.vertices, m_small.faces, kf[i].K, inv_T(st.T_wc[i]), kf[i].rgb_size)
        ious.append((sil & masks[i]).sum() / max(1, (sil | masks[i]).sum()))
        ratios.append(masks[i].sum() / max(1, sil.sum()))
    if ious:
        q["silhouette_iou_median"] = float(np.median(ious))
        q["silhouette_iou_p10"] = float(np.percentile(ious, 10))
        q["outline_to_model_area_ratio"] = float(np.median(ratios))
        if q["silhouette_iou_median"] < 0.9 or abs(q["outline_to_model_area_ratio"] - 1) > 0.05:
            what = ("smaller" if q["outline_to_model_area_ratio"] > 1 else "larger")
            res.warnings.append(f"The 3D model is noticeably {what} than the part's outline in the photos "
                                f"(agreement {q['silhouette_iou_median']:.2f}). The depth data was probably biased "
                                "(shiny, dark or very small part?). Do not trust the dimensions without a caliper "
                                "check; scanning spray and softer light usually fix this.")
    T_cp, rect = canonical_frame(mesh)
    mesh.apply_transform(T_cp)
    T_wc_c = np.array([T_cp @ T for T in st.T_wc])
    P(0.4, "colouring")
    views = [(kf[i].rgb, kf[i].K, inv_T(T_wc_c[i]), depths[i][0], depths[i][1]) for i in range(len(kf))]
    q["color_coverage"] = colorize(mesh, views)
    del views
    mesh_mm = mesh.copy()
    mesh_mm.apply_scale(1000.0)
    P(0.6, "measuring")
    sdf_vol_mm3 = _sdf_volume(vol, voxel)
    meas = measure_all(mesh_mm, cfg.material, sdf_volume_mm3=sdf_vol_mm3)
    # optional caliper reference
    ref = reference_check(meas, cfg)
    if ref is not None:
        meas["reference"] = ref
        if cfg.apply_reference_scale and ref.get("ratio") and not ref["name"].endswith("diameter") \
                and abs(ref["ratio"] - 1) <= 0.03:
            s = ref["ratio"]
            mesh.apply_scale(s)
            mesh_mm.apply_scale(s)
            meas = measure_all(mesh_mm, cfg.material, sdf_volume_mm3=sdf_vol_mm3 * s ** 3)
            ref["applied"] = True
            meas["reference"] = ref
            T_wc_c = T_wc_c.copy()
            T_wc_c[:, :3, 3] *= s          # keep the QA overlays consistent with the rescaled model
        elif cfg.apply_reference_scale:
            res.warnings.append("Rescaling skipped: only length/width/height can be used, and only for "
                                "corrections up to 3 % (larger differences mean a capture or entry problem).")
    res.measurements = meas
    rough = [c for c in meas.get("circles", []) if c["kind"] == "hole" and c.get("rough")]
    if rough:
        res.warnings.append(f"{len(rough)} hole(s) (axis {', '.join(sorted({c['axis'] for c in rough}))}) were only "
                            "partly captured: their outlines are ragged and the diameters (marked 'rough') are "
                            "uncertain and probably too small. Add views looking straight into them, or measure "
                            "them directly.")
    if "mesh" in hooks:
        hooks["mesh"](mesh_mm, meas)

    # ------------------------------------------------------------------ 8. export
    P.stage("export")
    from . import drawings
    from . import export as exportmod
    pre = {}
    try:   # the silhouette check needs the photos and masks: make it, then free all per-frame data
        pre["qa"] = drawings.qa_montage(kf, masks, T_wc_c, mesh, os.path.join(out_dir, "qa_silhouettes.jpg"))
    except Exception as ex:
        log.warning("QA montage failed: %s", ex)
    del depths, depth_masks, vol
    masks = None
    for f in scan.frames:
        f.rgb = f.depth = f.conf = f.rgb_bytes = None
        f.extra.clear()
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    q["seconds"] = round(time.time() - t_start, 1)
    res.files = exportmod.export_all(out_dir, mesh, mesh_mm, meas, q, res.warnings, cfg, qa=None,
                                     progress=P.sub(0, 1), extra_files=pre)
    res.seconds = time.time() - t_start
    if progress is not None:
        progress(1.0, "done")
    return res


def _sdf_volume(vol, voxel):
    """Solid volume [mm^3] straight from the fused SDF (partial-voxel occupancy)."""
    occ = np.clip(0.5 - vol / voxel, 0.0, 1.0)
    return float(occ.sum() * (voxel * 1000.0) ** 3)


REFERENCE_CHOICES = ["Length (X)", "Width (Y)", "Height (Z)", "Largest hole diameter", "Smallest hole diameter"]


def reference_check(meas: dict, cfg: Config):
    if not cfg.reference_mm or cfg.reference_mm <= 0 or not cfg.reference_name:
        return None
    from .measure import best_dims
    L, W, H, _ = best_dims(meas)
    holes = [c for c in meas.get("circles", []) if c["kind"] == "hole"]
    holes = [c for c in holes if not c.get("rough")] or holes     # compare with cleanly measured holes first
    name = cfg.reference_name
    val = {"Length (X)": L, "Width (Y)": W, "Height (Z)": H}.get(name)
    if name == "Largest hole diameter" and holes:
        val = max(h["diameter"] for h in holes)
    if name == "Smallest hole diameter" and holes:
        val = min(h["diameter"] for h in holes)
    if val is None:
        return dict(name=name, reference_mm=cfg.reference_mm, measured_mm=None, error_mm=None, ratio=None)
    return dict(name=name, reference_mm=float(cfg.reference_mm), measured_mm=float(val),
                error_mm=float(val - cfg.reference_mm), error_pct=float((val / cfg.reference_mm - 1) * 100),
                ratio=float(cfg.reference_mm / val), applied=False)
