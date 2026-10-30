"""Photogrammetric pose refinement.

* SIFT (RootSIFT) features, mutual-NN + ratio matching (GPU if available), MAGSAC verification
* multi-view tracks (union-find) and DLT triangulation
* sparse bundle adjustment (scipy) mixing three kinds of evidence:
    - ChArUco corners (known 3D points -> metric scale, no drift)
    - feature tracks on/around the part
    - ARKit relative motion between consecutive keyframes (keeps weakly observed frames sane)
  optionally refining a global focal correction, principal point shift and radial distortion.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from .geometry import inv_T

log = logging.getLogger("partscan")


# --------------------------------------------------------------------------- features
def extract_features(gray: np.ndarray, n_features: int, mask=None):
    sift = cv2.SIFT_create(nfeatures=n_features, contrastThreshold=0.015, edgeThreshold=12)
    kps, desc = sift.detectAndCompute(gray, mask)
    if desc is None or len(kps) == 0:
        return np.zeros((0, 2)), np.zeros((0, 128), np.float32)
    desc = desc / (desc.sum(1, keepdims=True) + 1e-7)        # RootSIFT
    desc = np.sqrt(desc).astype(np.float32)
    pts = np.array([k.pt for k in kps], np.float64)
    return pts, desc


def _match_pair(d1, d2, ratio=0.82, device=None):
    """Mutual nearest neighbours with Lowe ratio on unit-norm descriptors."""
    if len(d1) < 2 or len(d2) < 2:
        return np.zeros((0, 2), int)
    try:
        import torch
        dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
        a = torch.from_numpy(d1).to(dev)
        b = torch.from_numpy(d2).to(dev)
        sim = a @ b.T
        top2 = sim.topk(2, dim=1)
        nn12 = top2.indices[:, 0]
        nn21 = sim.argmax(0)
        # ||a-b||^2 = 2 - 2 sim  (unit vectors)
        d_best = (2 - 2 * top2.values[:, 0]).clamp_min(0).sqrt()
        d_second = (2 - 2 * top2.values[:, 1]).clamp_min(0).sqrt()
        ok = (d_best < ratio * d_second) & (nn21[nn12] == torch.arange(len(a), device=dev))
        i1 = torch.nonzero(ok).ravel()
        return torch.stack([i1, nn12[i1]], 1).cpu().numpy()
    except ImportError:
        sim = d1 @ d2.T
        nn12 = sim.argmax(1)
        nn21 = sim.argmax(0)
        s_sorted = np.sort(sim, 1)
        d_best = np.sqrt(np.clip(2 - 2 * s_sorted[:, -1], 0, None))
        d_second = np.sqrt(np.clip(2 - 2 * s_sorted[:, -2], 0, None))
        ok = (d_best < ratio * d_second) & (nn21[nn12] == np.arange(len(d1)))
        i1 = np.nonzero(ok)[0]
        return np.stack([i1, nn12[i1]], 1)


def select_pairs(T_wc: list, n_neighbors: int, max_angle: float = 40.0):
    """Frame pairs with similar viewing directions (likely to share features)."""
    F = len(T_wc)
    z = np.array([T[:3, 2] for T in T_wc])
    c = np.array([T[:3, 3] for T in T_wc])
    ang = np.degrees(np.arccos(np.clip(z @ z.T, -1, 1)))
    pairs = set()
    for i in range(F):
        order = np.argsort(ang[i])
        cnt = 0
        for j in order:
            if j == i or ang[i, j] > max_angle:
                continue
            if np.linalg.norm(c[i] - c[j]) < 0.004 and ang[i, j] < 1.0:
                continue  # nearly identical viewpoint: no triangulation value
            pairs.add((min(i, j), max(i, j)))
            cnt += 1
            if cnt >= n_neighbors:
                break
    return sorted(pairs)


class _UF:
    def __init__(self, n):
        self.p = np.arange(n)

    def find(self, a):
        p = self.p
        root = a
        while p[root] != root:
            root = p[root]
        while p[a] != root:
            p[a], a = root, p[a]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


@dataclass
class Tracks:
    obs_cam: np.ndarray        # (M,) frame index
    obs_pt: np.ndarray         # (M,) track index
    obs_uv: np.ndarray         # (M,2) pixel
    X: np.ndarray              # (P,3) world points
    n_points: int = 0
    stats: dict = field(default_factory=dict)


def build_tracks(feats: list, pairs: list, device=None, min_inliers: int = 20, progress=None):
    """Match features over the pairs and chain the verified matches into multi-view tracks."""
    offs = np.cumsum([0] + [len(p) for p, _ in feats])
    uf = _UF(int(offs[-1]))
    n_ok = 0
    for k, (i, j) in enumerate(pairs):
        (p1, d1), (p2, d2) = feats[i], feats[j]
        m = _match_pair(d1, d2, device=device)
        if len(m) < min_inliers:
            continue
        F, inl = cv2.findFundamentalMat(p1[m[:, 0]], p2[m[:, 1]], cv2.USAC_MAGSAC, 1.5, 0.999, 5000)
        if F is None or inl is None:
            continue
        m = m[inl.ravel().astype(bool)]
        if len(m) < min_inliers:
            continue
        n_ok += 1
        for a, b in m:
            uf.union(offs[i] + a, offs[j] + b)
        if progress is not None and k % 20 == 0:
            progress(k / max(1, len(pairs)), f"matching pair {k}/{len(pairs)}")
    roots = np.array([uf.find(a) for a in range(int(offs[-1]))])
    cam_of = np.searchsorted(offs, np.arange(int(offs[-1])), side="right") - 1
    kp_of = np.arange(int(offs[-1])) - offs[cam_of]
    order = np.argsort(roots, kind="stable")
    r_sorted = roots[order]
    starts = np.r_[0, np.nonzero(np.diff(r_sorted))[0] + 1]
    ends = np.r_[starts[1:], len(order)]
    tracks = []
    for s, e in zip(starts, ends):
        if e - s < 2:
            continue
        members = order[s:e]
        cams = cam_of[members]
        if len(np.unique(cams)) != len(cams):
            continue  # inconsistent track (two features of one image)
        tracks.append([(int(c), feats[c][0][kp_of[mm]]) for c, mm in zip(cams, members)])
    return tracks, n_ok


# --------------------------------------------------------------------------- triangulation
def triangulate_tracks(tracks: list, Ks: np.ndarray, T_cw: np.ndarray, min_views: int = 3,
                       max_reproj: float = 3.0, min_angle_deg: float = 2.0):
    """Multi-view DLT for every track; drops bad observations / points. Returns Tracks."""
    obs_cam, obs_pt, obs_uv, Xs = [], [], [], []
    centers = inv_T(T_cw)[:, :3, 3]
    P = np.einsum("fij,fjk->fik", Ks, T_cw[:, :3, :])
    by_len = {}
    for t in tracks:
        if len(t) >= min_views:
            by_len.setdefault(len(t), []).append(t)
    for L, group in by_len.items():
        cams = np.array([[c for c, _ in t] for t in group])            # (T,L)
        uv = np.array([[p for _, p in t] for t in group])               # (T,L,2)
        Pm = P[cams]                                                    # (T,L,3,4)
        A = np.concatenate([uv[..., 0:1] * Pm[..., 2, :] - Pm[..., 0, :],
                            uv[..., 1:2] * Pm[..., 2, :] - Pm[..., 1, :]], axis=1)  # (T,2L,4)
        A = A / (np.linalg.norm(A, axis=2, keepdims=True) + 1e-12)
        _, _, Vt = np.linalg.svd(A)
        Xh = Vt[:, -1, :]
        X = Xh[:, :3] / np.where(np.abs(Xh[:, 3:]) < 1e-12, 1e-12, Xh[:, 3:])
        # reprojection per observation
        Xc = np.einsum("tlij,tj->tli", T_cw[cams][..., :3, :3], X) + T_cw[cams][..., :3, 3]
        z = Xc[..., 2]
        proj = np.einsum("tlij,tlj->tli", Ks[cams], Xc / np.where(np.abs(z) < 1e-9, 1e-9, z)[..., None])[..., :2]
        err = np.linalg.norm(proj - uv, axis=2)
        good = (err < max_reproj) & (z > 0.02)
        # triangulation angle
        rays = X[:, None, :] - centers[cams]
        rays /= np.linalg.norm(rays, axis=2, keepdims=True) + 1e-12
        cosang = np.einsum("tli,tmi->tlm", rays, rays)
        cosang = np.where(good[:, :, None] & good[:, None, :], cosang, 1.0)
        max_ang = np.degrees(np.arccos(np.clip(cosang.min(axis=(1, 2)), -1, 1)))
        keep = (good.sum(1) >= min_views) & (max_ang >= min_angle_deg)
        for ti in np.nonzero(keep)[0]:
            pid = len(Xs)
            Xs.append(X[ti])
            for l in np.nonzero(good[ti])[0]:
                obs_cam.append(cams[ti, l])
                obs_pt.append(pid)
                obs_uv.append(uv[ti, l])
    if not Xs:
        return Tracks(np.zeros(0, int), np.zeros(0, int), np.zeros((0, 2)), np.zeros((0, 3)), 0)
    return Tracks(np.array(obs_cam), np.array(obs_pt), np.array(obs_uv), np.array(Xs), len(Xs))


# --------------------------------------------------------------------------- bundle adjustment
@dataclass
class BAResult:
    T_wc: np.ndarray
    X: np.ndarray
    intr: np.ndarray            # [dfocal, dcx, dcy, k1, k2]
    rms_board: float
    rms_tracks: float
    n_board_obs: int
    n_track_obs: int
    iterations: int
    cost0: float
    cost: float
    track_err: np.ndarray = None   # per track observation reprojection error [px]
    board_err: np.ndarray = None


def _skew(v):
    O = np.zeros(v.shape[:-1] + (3, 3))
    O[..., 0, 1], O[..., 0, 2] = -v[..., 2], v[..., 1]
    O[..., 1, 0], O[..., 1, 2] = v[..., 2], -v[..., 0]
    O[..., 2, 0], O[..., 2, 1] = -v[..., 1], v[..., 0]
    return O


def _right_jac(r):
    th = np.linalg.norm(r, axis=1)
    S = _skew(r)
    small = th < 1e-7
    t = np.where(small, 1.0, th)
    a = np.where(small, 0.5, (1 - np.cos(t)) / t ** 2)
    b = np.where(small, 1.0 / 6.0, (t - np.sin(t)) / t ** 3)
    return np.eye(3)[None] - a[:, None, None] * S + b[:, None, None] * (S @ S)


INTR_NAMES = ("dfocal", "dcx", "dcy", "k1", "k2")


def _scatter2(cols, blocks, N):
    """Sum blocks[m] (C,C) into an (N,N) matrix at rows/cols cols[m] (C,)."""
    idx = cols[:, :, None] * N + cols[:, None, :]
    return np.bincount(idx.ravel(), blocks.ravel(), minlength=N * N).reshape(N, N)


def _scatter_blocks(index, blocks, P):
    """Sum (M,3,3) blocks into (P,3,3) by point index."""
    flat = (index[:, None] * 9 + np.arange(9)).ravel()
    return np.bincount(flat, blocks.reshape(-1, 9).ravel(), minlength=P * 9).reshape(P, 3, 3)


def bundle_adjust(Ks: np.ndarray, T_wc0: np.ndarray, board_obs: dict | None = None,
                  tracks: Tracks | None = None, rel_priors: list | None = None,
                  abs_priors: np.ndarray | None = None, refine_intrinsics=False,
                  intr0=None, intr_sigma=None, huber_px: float = 1.0, max_iter: int = 40,
                  verbose: bool = False, fix_poses=False) -> BAResult:
    """Levenberg-Marquardt bundle adjustment with analytic Jacobians and a Schur complement
    (points eliminated), so it stays fast with tens of thousands of points.

    Ks          (F,3,3) per-frame intrinsics (ARKit; can vary with focus)
    T_wc0       (F,4,4) initial camera->world poses
    board_obs   {frame: (uv (N,2), XYZ_world (N,3))}  known 3D points (ChArUco corners)
    tracks      Tracks; its world points X are optimised
    rel_priors  [(i, j, T_ji, sigma_rot_rad, sigma_t_m)]  soft relative-motion constraints
    abs_priors  (F,3) expected camera centres (2 cm sigma) to fix the gauge without a board
    refine_intrinsics  False | True (all five) | iterable of names from INTR_NAMES
    intr_sigma  prior sigmas for the five global intrinsic corrections
    fix_poses   False | True (only points move) | (F,) bool mask of frames whose pose stays fixed |
                (F, 6) mask per pose parameter [rx ry rz tx ty tz]
    """
    from scipy.sparse import csr_matrix
    from scipy.linalg import cho_factor, cho_solve

    F = len(T_wc0)
    T_cw0 = inv_T(np.asarray(T_wc0, float))
    r = Rotation.from_matrix(T_cw0[:, :3, :3]).as_rotvec()
    t = T_cw0[:, :3, 3].copy()
    intr = np.zeros(5) if intr0 is None else np.asarray(intr0, float).copy()
    if refine_intrinsics is True:
        active = list(range(5))
    elif refine_intrinsics:
        active = [INTR_NAMES.index(n) for n in refine_intrinsics]
    else:
        active = []
    nI = len(active)
    isig = np.array([0.003, 4.0, 4.0, 0.05, 0.01]) if intr_sigma is None else np.asarray(intr_sigma, float)
    intr_prior = intr.copy()

    # flatten observations
    bc, buv, bX = [], [], []
    for i, (uv, X) in (board_obs or {}).items():
        bc.append(np.full(len(uv), i))
        buv.append(uv)
        bX.append(X)
    bc = np.concatenate(bc).astype(int) if bc else np.zeros(0, int)
    buv = np.concatenate(buv) if buv else np.zeros((0, 2))
    bX = np.concatenate(bX) if bX else np.zeros((0, 3))
    use_t = tracks is not None and tracks.n_points > 0
    tc = tracks.obs_cam.astype(int) if use_t else np.zeros(0, int)
    tp = tracks.obs_pt.astype(int) if use_t else np.zeros(0, int)
    tuv = tracks.obs_uv if use_t else np.zeros((0, 2))
    X = tracks.X.copy() if use_t else np.zeros((0, 3))
    P = len(X)
    rel = rel_priors or []
    fxa, fya, cxa, cya = Ks[:, 0, 0], Ks[:, 1, 1], Ks[:, 0, 2], Ks[:, 1, 2]
    Nc = 6 * F + nI
    if fix_poses is False or fix_poses is True:
        fixed = np.full((F, 6), bool(fix_poses))
    else:                            # (F,) per frame or (F, 6) per parameter [rx ry rz tx ty tz]
        fixed = np.asarray(fix_poses, bool)
        fixed = np.repeat(fixed[:, None], 6, 1) if fixed.ndim == 1 else fixed
    free = np.r_[~fixed.ravel(), np.ones(nI, bool)]     # camera unknowns that move

    def reproj(R, t_, Xw_b, Xw_t, intr_, with_jac):
        out = []
        for cams, Xw, uv, has_pt in ((bc, Xw_b, buv, False), (tc, Xw_t, tuv, True)):
            if len(cams) == 0:
                out.append(None)
                continue
            Rc = R[cams]
            Xc = np.einsum("nij,nj->ni", Rc, Xw) + t_[cams]
            z = Xc[:, 2]
            z = np.where(np.abs(z) < 1e-6, 1e-6, z)
            x, y = Xc[:, 0] / z, Xc[:, 1] / z
            r2 = x * x + y * y
            k1, k2 = intr_[3], intr_[4]
            d = 1 + k1 * r2 + k2 * r2 * r2
            S = 1 + intr_[0]
            fx, fy = fxa[cams] * S, fya[cams] * S
            u = fx * x * d + cxa[cams] + intr_[1]
            v = fy * y * d + cya[cams] + intr_[2]
            res = np.stack([u, v], 1) - uv
            if not with_jac:
                out.append((res, None, None, None))
                continue
            dd = 2 * (k1 + 2 * k2 * r2)
            A = np.empty((len(cams), 2, 2))
            A[:, 0, 0] = fx * (d + x * dd * x)
            A[:, 0, 1] = fx * x * dd * y
            A[:, 1, 0] = fy * y * dd * x
            A[:, 1, 1] = fy * (d + y * dd * y)
            B = np.zeros((len(cams), 2, 3))
            B[:, 0, 0] = 1 / z
            B[:, 0, 2] = -x / z
            B[:, 1, 1] = 1 / z
            B[:, 1, 2] = -y / z
            G = A @ B                                                   # d(uv)/dXc
            dXdr = -Rc @ _skew(Xw) @ Jr[cams]
            Jpose = np.concatenate([G @ dXdr, G], 2)                    # (M,2,6)
            Jint = np.zeros((len(cams), 2, 5))
            Jint[:, 0, 0] = fxa[cams] * x * d
            Jint[:, 1, 0] = fya[cams] * y * d
            Jint[:, 0, 1] = 1
            Jint[:, 1, 2] = 1
            Jint[:, 0, 3] = fx * x * r2
            Jint[:, 1, 3] = fy * y * r2
            Jint[:, 0, 4] = fx * x * r2 * r2
            Jint[:, 1, 4] = fy * y * r2 * r2
            Jpt = G @ Rc if has_pt else None
            out.append((res, Jpose, Jint[:, :, active], Jpt))
        return out

    def rel_res(ri, ti, rj, tj):
        Ri = Rotation.from_rotvec(ri).as_matrix()
        Rj = Rotation.from_rotvec(rj).as_matrix()
        Rest = Rj @ np.swapaxes(Ri, 1, 2)
        test = tj - np.einsum("nij,nj->ni", Rest, ti)
        e_r = Rotation.from_matrix(Rest @ np.swapaxes(rel_R, 1, 2)).as_rotvec() / rel_sr[:, None]
        e_t = (test - rel_t) / rel_st[:, None]
        return np.concatenate([e_r, e_t], 1)

    def abs_res(r_, t_):
        R = Rotation.from_rotvec(r_).as_matrix()
        C = -np.einsum("nji,nj->ni", R, t_)
        return (C - abs_priors) / 0.02

    if rel:
        rel_i = np.array([p[0] for p in rel])
        rel_j = np.array([p[1] for p in rel])
        rel_R = np.array([p[2][:3, :3] for p in rel])
        rel_t = np.array([p[2][:3, 3] for p in rel])
        rel_sr = np.array([p[3] for p in rel])
        rel_st = np.array([p[4] for p in rel])

    def robust(res):
        e = np.linalg.norm(res, axis=1)
        w = np.where(e <= huber_px, 1.0, huber_px / np.maximum(e, 1e-12))
        cost = np.where(e <= huber_px, 0.5 * e * e, huber_px * (e - 0.5 * huber_px))
        return w, cost.sum()

    def total_cost(r_, t_, X_, intr_):
        R = Rotation.from_rotvec(r_).as_matrix()
        c = 0.0
        for item in reproj(R, t_, bX, X_[tp] if use_t else None, intr_, False):
            if item is not None:
                c += robust(item[0])[1]
        if rel:
            c += 0.5 * (rel_res(r_[rel_i], t_[rel_i], r_[rel_j], t_[rel_j]) ** 2).sum()
        if abs_priors is not None:
            c += 0.5 * (abs_res(r_, t_) ** 2).sum()
        if nI:
            c += 0.5 * (((intr_ - intr_prior)[active] / isig[active]) ** 2).sum()
        return c

    lam = 1e-3
    cost = total_cost(r, t, X, intr)
    cost0 = cost
    it = 0
    for it in range(max_iter):
        R = Rotation.from_rotvec(r).as_matrix()
        Jr = _right_jac(r)
        t_cur = t
        items = reproj(R, t, bX, X[tp] if use_t else None, intr, True)
        Hcc = np.zeros((Nc, Nc))
        gc = np.zeros(Nc)
        V = np.zeros((P, 3, 3))
        gp = np.zeros((P, 3))
        Hcp = None
        for k, item in enumerate(items):
            if item is None:
                continue
            res, Jpose, Jint, Jpt = item
            cams = bc if k == 0 else tc
            w, _ = robust(res)
            sw = np.sqrt(w)[:, None]
            res_w = res * sw
            Jpose = Jpose * sw[:, :, None]
            Jint = Jint * sw[:, :, None]
            Jc = np.concatenate([Jpose, Jint], 2)                     # (M,2,6+nI)
            cols = np.concatenate([6 * cams[:, None] + np.arange(6), np.broadcast_to(6 * F + np.arange(nI), (len(cams), nI))], 1)
            blk = np.einsum("mai,maj->mij", Jc, Jc)
            Hcc += _scatter2(cols, blk, Nc)
            gc += np.bincount(cols.ravel(), np.einsum("mai,ma->mi", Jc, res_w).ravel(), minlength=Nc)
            if k == 1:
                Jp = Jpt * sw[:, :, None]
                V += _scatter_blocks(tp, np.einsum("mai,maj->mij", Jp, Jp), P)
                gp += np.bincount((3 * tp[:, None] + np.arange(3)).ravel(), np.einsum("mai,ma->mi", Jp, res_w).ravel(),
                                  minlength=3 * P).reshape(P, 3)
                hcp = np.einsum("mai,maj->mij", Jc, Jp)                 # (M, 6+nI, 3)
                rows = np.repeat(cols[:, :, None], 3, 2).ravel()
                cl = np.repeat((3 * tp[:, None] + np.arange(3))[:, None, :], cols.shape[1], 1).ravel()
                Hcp = csr_matrix((hcp.ravel(), (rows, cl)), shape=(Nc, 3 * P))
        # priors (numeric Jacobians, vectorised)
        if rel:
            e0 = rel_res(r[rel_i], t[rel_i], r[rel_j], t[rel_j])
            Jrel = np.zeros((len(rel), 6, 12))
            h = 1e-6
            for s in range(12):
                args = [r[rel_i].copy(), t[rel_i].copy(), r[rel_j].copy(), t[rel_j].copy()]
                slot, comp = divmod(s, 3)
                args[slot][:, comp] += h
                ep = rel_res(*args)
                args[slot][:, comp] -= 2 * h
                em = rel_res(*args)
                Jrel[:, :, s] = (ep - em) / (2 * h)
            # parameter order inside one pose block is [r(3), t(3)]
            colmap = np.concatenate([6 * rel_i[:, None] + np.arange(3), 6 * rel_i[:, None] + 3 + np.arange(3),
                                     6 * rel_j[:, None] + np.arange(3), 6 * rel_j[:, None] + 3 + np.arange(3)], 1)
            Hcc += _scatter2(colmap, np.einsum("nai,naj->nij", Jrel, Jrel), Nc)
            gc += np.bincount(colmap.ravel(), np.einsum("nai,na->ni", Jrel, e0).ravel(), minlength=Nc)
        if abs_priors is not None:
            e0 = abs_res(r, t)
            Jab = np.zeros((F, 3, 6))
            h = 1e-6
            for s in range(6):
                rr, tt = r.copy(), t.copy()
                (rr if s < 3 else tt)[:, s % 3] += h
                ep = abs_res(rr, tt)
                (rr if s < 3 else tt)[:, s % 3] -= 2 * h
                em = abs_res(rr, tt)
                Jab[:, :, s] = (ep - em) / (2 * h)
            colmap = 6 * np.arange(F)[:, None] + np.arange(6)
            Hcc += _scatter2(colmap, np.einsum("nai,naj->nij", Jab, Jab), Nc)
            gc += np.bincount(colmap.ravel(), np.einsum("nai,na->ni", Jab, e0).ravel(), minlength=Nc)
        if nI:
            ii = 6 * F + np.arange(nI)
            Hcc[ii, ii] += 1.0 / isig[active] ** 2
            gc[ii] += (intr - intr_prior)[active] / isig[active] ** 2

        # LM step with Schur complement
        improved = False
        for _try in range(10):
            Hd = Hcc + lam * np.diag(np.diag(Hcc) + 1e-9)
            if P:
                Vd = V + lam * (V * np.eye(3)[None]) + 1e-12 * np.eye(3)[None]
                Vinv = np.linalg.inv(Vd)
                rows = (3 * np.arange(P)[:, None, None] + np.arange(3)[None, :, None]).repeat(3, 2).ravel()
                cols = (3 * np.arange(P)[:, None, None] + np.arange(3)[None, None, :]).repeat(3, 1).ravel()
                Vi = csr_matrix((Vinv.ravel(), (rows, cols)), shape=(3 * P, 3 * P))
                HV = Hcp @ Vi
                Sm = Hd - (HV @ Hcp.T).toarray()
                rhs = -gc + HV @ gp.ravel()
            else:
                Sm, rhs = Hd, -gc
            dc = np.zeros(Nc)
            if free.any():          # fixed poses: solve the reduced camera system for the free unknowns only
                try:
                    dc[free] = cho_solve(cho_factor(Sm[np.ix_(free, free)]), rhs[free])
                except np.linalg.LinAlgError:
                    lam *= 10
                    continue
            dp = (Vi @ (-gp.ravel() - Hcp.T @ dc)).reshape(P, 3) if P else np.zeros((0, 3))
            r_n = r + dc[:6 * F].reshape(F, 6)[:, :3]
            t_n = t + dc[:6 * F].reshape(F, 6)[:, 3:]
            intr_n = intr.copy()
            if nI:
                intr_n[active] += dc[6 * F:]
            X_n = X + dp
            c_n = total_cost(r_n, t_n, X_n, intr_n)
            if c_n < cost:
                rel_dec = (cost - c_n) / max(cost, 1e-12)
                r, t, X, intr, cost = r_n, t_n, X_n, intr_n, c_n
                lam = max(lam / 3, 1e-9)
                improved = True
                break
            lam *= 4
        if verbose:
            log.info("BA it %d cost %.4g lam %.1e", it, cost, lam)
        if not improved or rel_dec < 1e-7:
            break

    R = Rotation.from_rotvec(r).as_matrix()
    t_cur = t
    items = reproj(R, t, bX, X[tp] if use_t else None, intr, False)
    berr = np.linalg.norm(items[0][0], axis=1) if items[0] is not None else np.zeros(0)
    terr = np.linalg.norm(items[1][0], axis=1) if items[1] is not None else np.zeros(0)
    T_cw = np.tile(np.eye(4), (F, 1, 1))
    T_cw[:, :3, :3] = R
    T_cw[:, :3, 3] = t
    rms = lambda e: float(np.sqrt(np.mean(e ** 2))) if len(e) else float("nan")
    return BAResult(inv_T(T_cw), X, intr, rms(berr), rms(np.minimum(terr, 5.0)), len(berr), len(terr),
                    it + 1, float(cost0), float(cost), terr, berr)


def relative_priors(T_wc_arkit: np.ndarray, scale: float, sigma_rot_deg: float = 0.3, sigma_t_mm: float = 1.5):
    """ARKit relative motion between consecutive keyframes as soft BA constraints.
    Sigma grows with the size of the step (VIO error accumulates with motion)."""
    pri = []
    T_cw = inv_T(T_wc_arkit)
    for i in range(len(T_wc_arkit) - 1):
        j = i + 1
        Tji = T_cw[j] @ T_wc_arkit[i]
        Tji = Tji.copy()
        Tji[:3, 3] *= scale
        step = np.linalg.norm(Tji[:3, 3])
        st = sigma_t_mm / 1000.0 + 0.02 * step
        sr = np.radians(sigma_rot_deg) + 0.01 * np.linalg.norm(Rotation.from_matrix(Tji[:3, :3]).as_rotvec())
        pri.append((i, j, Tji, sr, st))
    return pri


def undistort_maps(K_src: np.ndarray, intr: np.ndarray, size_wh, K_dst: np.ndarray | None = None):
    """Remap tables that turn an image taken with (K_src, intr) into an ideal pinhole image
    with intrinsics K_dst (default: the corrected focal/principal point, no distortion)."""
    s = 1 + intr[0]
    Kc = K_src.copy()
    Kc[0, 0] *= s
    Kc[1, 1] *= s
    Kc[0, 2] += intr[1]
    Kc[1, 2] += intr[2]
    K_dst = Kc if K_dst is None else K_dst
    dist = np.array([intr[3], intr[4], 0, 0, 0], float)
    m1, m2 = cv2.initUndistortRectifyMap(Kc, dist, None, K_dst, tuple(int(v) for v in size_wh), cv2.CV_32FC1)
    return m1, m2, K_dst
