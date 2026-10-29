"""Camera pose estimation: ChArUco board (if present) + photogrammetric refinement."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import bundle
from .board import board_to_part, detect, make_board, make_detector, robust_pnp
from .config import Config
from .geometry import apply_sim3_to_pose, inv_T, project, umeyama_sim3

log = logging.getLogger("partscan")


@dataclass
class PoseState:
    world: str                       # "board" (part frame from the board) or "arkit"
    T_wc: np.ndarray                 # (F,4,4) current best poses
    T_wc_arkit: np.ndarray           # (F,4,4) raw ARKit poses
    board_obs: dict = field(default_factory=dict)   # i -> (uv, XYZ_world)
    arkit_scale: float = 1.0         # world metres per ARKit metre (from the board)
    intr: np.ndarray = field(default_factory=lambda: np.zeros(5))
    undistorted: bool = False
    stats: dict = field(default_factory=dict)
    tracks: object = None            # bundle.Tracks after feature BA


def detect_board(kf, cfg: Config, progress=None) -> dict:
    spec = cfg.board
    board = make_board(spec)
    det = make_detector(board)
    T_pb = board_to_part(spec)
    out = {}
    for i, f in enumerate(kf):
        gray = cv2.cvtColor(f.load_rgb(), cv2.COLOR_RGB2GRAY)
        d = detect(gray, board, det)
        if progress is not None and i % 10 == 0:
            progress(i / len(kf), f"looking for the calibration board {i}/{len(kf)}")
        if d is None or len(d[0]) < cfg.min_board_corners:
            continue
        uv, ids, obj = d
        T_cb, keep, rms = robust_pnp(obj, uv, f.K)
        if T_cb is None or keep.sum() < cfg.min_board_corners or rms > 2.5:
            continue
        out[i] = dict(uv=uv[keep], X=obj[keep] @ T_pb[:3, :3].T + T_pb[:3, 3], T_wc=T_pb @ inv_T(T_cb), rms=rms)
    return out


def init_poses(kf, cfg: Config, progress=None) -> PoseState:
    T_ark = np.array([f.T_wc for f in kf])
    det = detect_board(kf, cfg, progress) if cfg.use_board else {}
    n_ok = len(det)
    if n_ok >= max(6, 0.25 * len(kf)):
        ids = sorted(det)
        s, R, t = umeyama_sim3(T_ark[ids, :3, 3], np.array([det[i]["T_wc"][:3, 3] for i in ids]))
        T0 = np.array([apply_sim3_to_pose(s, R, t, T) for T in T_ark])
        for i in ids:
            T0[i] = det[i]["T_wc"]
        st = PoseState("board", T0, T_ark, {i: (det[i]["uv"], det[i]["X"]) for i in ids}, float(s))
        st.stats.update(board_frames=n_ok, board_pnp_rms_px=float(np.median([det[i]["rms"] for i in ids])),
                        arkit_scale_error_pct=float((1.0 / s - 1.0) * 100.0))
        log.info("board found in %d/%d keyframes; phone scale error vs board %.2f%%", n_ok, len(kf),
                 st.stats["arkit_scale_error_pct"])
        return st
    if cfg.use_board and n_ok:
        log.warning("board seen in only %d/%d keyframes - ignoring it", n_ok, len(kf))
    st = PoseState("arkit", T_ark.copy(), T_ark, {}, 1.0)
    st.stats.update(board_frames=n_ok)
    return st


def refine_with_board(kf, st: PoseState, cfg: Config) -> PoseState:
    """Board-only BA (+ global focal/principal-point correction and radial distortion)."""
    Ks = np.array([f.K for f in kf])
    W = kf[0].rgb_size[0]
    pri = bundle.relative_priors(st.T_wc_arkit, st.arkit_scale)
    kw = dict(board_obs=st.board_obs, rel_priors=pri)
    res0 = bundle.bundle_adjust(Ks, st.T_wc, **kw)
    res = res0
    if cfg.refine_intrinsics:
        sig = np.array([0.003, 4.0 * W / 1920, 4.0 * W / 1920, 0.05, 0.01])
        res1 = bundle.bundle_adjust(Ks, st.T_wc, refine_intrinsics=("dfocal", "dcx", "dcy", "k1"),
                                    intr_sigma=sig, **kw)
        if res1.rms_board < 0.97 * res0.rms_board or abs(res1.intr[3]) > 0.004:
            res = res1
    st.T_wc = res.T_wc
    st.intr = res.intr
    st.stats.update(board_ba_rms_px=res.rms_board, board_ba_rms_px_no_intr=res0.rms_board,
                    k1=float(res.intr[3]), focal_correction_pct=float(res.intr[0] * 100))
    log.info("board BA: %.3f px (fixed intrinsics %.3f px), k1=%.4f", res.rms_board, res0.rms_board, res.intr[3])
    return st


def undistort_keyframes(kf, st: PoseState, min_k1: float = 0.002) -> None:
    """Resample RGB, LiDAR depth and confidence to an ideal pinhole camera (in place)."""
    intr = st.intr
    if abs(intr[3]) < min_k1 and abs(intr[4]) < min_k1 and abs(intr[0]) < 1e-4 and abs(intr[1]) < 0.05 and abs(intr[2]) < 0.05:
        return
    for f in kf:
        rgb = f.load_rgb()
        H, W = rgb.shape[:2]
        m1, m2, Kn = bundle.undistort_maps(f.K, intr, (W, H))
        f.extra["K_orig"] = f.K.copy()
        f.extra["undist_intr"] = np.asarray(intr, float).copy()
        valid = cv2.remap(np.ones((H, W), np.uint8), m1, m2, cv2.INTER_NEAREST, borderValue=0)
        f.rgb = cv2.remap(rgb, m1, m2, cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT)
        f.extra["valid"] = valid.astype(bool)
        if f.depth is not None:
            dh, dw = f.depth.shape
            from .geometry import K_for_size
            Knd = K_for_size(Kn, (W, H), (dw, dh))   # corrected pinhole model at depth resolution
            d1, d2, _ = bundle.undistort_maps(Knd, np.r_[0, 0, 0, intr[3:]], (dw, dh), K_dst=Knd)
            f.depth = cv2.remap(f.depth, d1, d2, cv2.INTER_NEAREST, borderValue=0)
            f.conf = cv2.remap(f.conf, d1, d2, cv2.INTER_NEAREST, borderValue=0)
        f.K = Kn
    # board observations must now be in the undistorted image
    for i, (uv, X) in list(st.board_obs.items()):
        st.board_obs[i] = (_undistort_px(uv, kf[i].K, st.intr), X)
    st.undistorted = True
    st.intr = np.zeros(5)


def _undistort_px(uv, K_new, intr):
    # K_new is the corrected pinhole matrix; invert the radial model in normalised coordinates
    xd = (uv - K_new[:2, 2]) / [K_new[0, 0], K_new[1, 1]]
    xu = xd.copy()
    for _ in range(10):
        r2 = (xu ** 2).sum(1, keepdims=True)
        xu = xd / (1 + intr[3] * r2 + intr[4] * r2 * r2)
    return xu * [K_new[0, 0], K_new[1, 1]] + K_new[:2, 2]


def roi_mask(f, T_wc, roi_min, roi_max, grow_px: int = 25):
    """Image mask covering the projection of an axis-aligned box (part frame)."""
    W, H = f.rgb_size
    c = np.array([[x, y, z] for x in (roi_min[0], roi_max[0]) for y in (roi_min[1], roi_max[1])
                  for z in (roi_min[2], roi_max[2])])
    uv, z = project(f.K, inv_T(T_wc), c)
    m = np.zeros((H, W), np.uint8)
    if (z <= 0.01).any():
        m[:] = 1
        return m
    hull = cv2.convexHull(uv.astype(np.float32)).astype(np.int32)
    cv2.fillConvexPoly(m, hull, 1)
    if grow_px:
        m = cv2.dilate(m, np.ones((2 * grow_px + 1, 2 * grow_px + 1), np.uint8))
    return m


def feature_refine(kf, st: PoseState, roi_min, roi_max, cfg: Config, progress=None) -> PoseState:
    """SIFT tracks around the part + joint bundle adjustment."""
    feats = []
    grow = np.array([0.04, 0.04, 0.0])
    for i, f in enumerate(kf):
        gray = cv2.cvtColor(f.load_rgb(), cv2.COLOR_RGB2GRAY)
        m = roi_mask(f, st.T_wc[i], roi_min - grow, roi_max + grow * [1, 1, 0])
        if "valid" in f.extra:
            m &= cv2.erode(f.extra["valid"].astype(np.uint8), np.ones((9, 9), np.uint8))
        feats.append(bundle.extract_features(gray, cfg.sift_features, m))
        if progress is not None and i % 10 == 0:
            progress(0.3 * i / len(kf), f"features {i}/{len(kf)}")
    pairs = bundle.select_pairs(list(st.T_wc), cfg.match_neighbors)
    raw_tracks, n_pairs = bundle.build_tracks(
        feats, pairs, progress=None if progress is None else (lambda p, m: progress(0.3 + 0.4 * p, m)))
    Ks = np.array([f.K for f in kf])
    T_cw = inv_T(st.T_wc)
    tracks = bundle.triangulate_tracks(raw_tracks, Ks, T_cw, min_views=3, max_reproj=6.0)
    if tracks.n_points > cfg.ba_max_points:
        keep_pts = _subsample_points(tracks, cfg.ba_max_points)
        tracks = _filter_tracks(tracks, np.isin(tracks.obs_pt, keep_pts))
    log.info("feature tracks: %d points (%d obs) from %d/%d verified pairs", tracks.n_points, len(tracks.obs_cam),
             n_pairs, len(pairs))
    if tracks.n_points < 50:
        st.stats["feature_points"] = int(tracks.n_points)
        log.warning("too few feature tracks for photogrammetric refinement")
        return st
    pri = bundle.relative_priors(st.T_wc_arkit, st.arkit_scale)
    absp = None if st.world == "board" else np.array([T[:3, 3] for T in st.T_wc])
    # With the board in (almost) every frame, the board-only solution is already the most accurate:
    # keep those poses and refine the tie points plus the few frames that did not see the board
    # (e.g. low views straight into a side hole) - those only have the phone's drifting tracking.
    # Their orientation from the board BA (board frames + the phone's relative motion) is already good to
    # a few hundredths of a degree; a handful of tie points can fix their position but would spoil it.
    fix = False
    if st.world == "board" and len(st.board_obs) >= 0.8 * len(kf):
        on_board = np.array([i in st.board_obs for i in range(len(kf))])
        fix = np.zeros((len(kf), 6), bool)
        fix[on_board] = True
        fix[~on_board, :3] = True
    st.stats["poses_fixed_to_board"] = fix is not False
    st.stats["poses_refined_without_board"] = int(len(kf) - len(st.board_obs)) if fix is not False else 0
    if progress is not None:
        progress(0.75, "bundle adjustment")
    res = bundle.bundle_adjust(Ks, st.T_wc, board_obs=st.board_obs, tracks=tracks, rel_priors=pri,
                               abs_priors=absp, huber_px=1.5, fix_poses=fix)
    # drop outliers, re-triangulate with the refined poses, polish
    good = res.track_err < 2.0
    tracks.X = res.X
    tracks = _filter_tracks(tracks, good)
    if progress is not None:
        progress(0.9, "bundle adjustment (polish)")
    res = bundle.bundle_adjust(Ks, res.T_wc, board_obs=st.board_obs, tracks=tracks, rel_priors=pri,
                               abs_priors=absp, huber_px=1.0, fix_poses=fix)
    tracks.X = res.X
    st.T_wc = res.T_wc
    st.tracks = tracks
    st.stats.update(feature_points=int(tracks.n_points), feature_obs=int(len(tracks.obs_cam)),
                    ba_rms_tracks_px=res.rms_tracks, ba_rms_board_px=res.rms_board)
    log.info("feature BA: tracks %.3f px, board %.3f px", res.rms_tracks, res.rms_board)
    return st


def _subsample_points(tracks, n):
    cnt = np.bincount(tracks.obs_pt, minlength=tracks.n_points)
    order = np.argsort(-cnt + np.random.default_rng(0).random(len(cnt)) * 0.5)
    return order[:n]


def _filter_tracks(tracks, obs_keep):
    """Keep selected observations; drop points left with < 2 views; re-index."""
    cam, pt, uv = tracks.obs_cam[obs_keep], tracks.obs_pt[obs_keep], tracks.obs_uv[obs_keep]
    cnt = np.bincount(pt, minlength=tracks.n_points)
    ok_pt = cnt >= 2
    m = ok_pt[pt]
    cam, pt, uv = cam[m], pt[m], uv[m]
    new_id = -np.ones(tracks.n_points, int)
    new_id[np.nonzero(ok_pt)[0]] = np.arange(ok_pt.sum())
    return bundle.Tracks(cam, new_id[pt], uv, tracks.X[ok_pt], int(ok_pt.sum()))
