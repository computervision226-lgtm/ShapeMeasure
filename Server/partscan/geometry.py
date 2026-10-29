"""Small, dependency-light geometry helpers shared by every stage.

Conventions used throughout PartScan
------------------------------------
* Units are metres internally; reports and exported meshes use millimetres.
* Camera poses are 4x4 camera-to-world matrices ``T_wc`` with OpenCV camera axes
  (x right, y down, z forward).  ``T_cw = inv(T_wc)``.
* Pixel coordinates follow OpenCV: the centre of the top-left pixel is (0, 0).
  When an image is resized by (sx, sy) the intrinsics change as
  ``fx' = fx*sx`` and ``cx' = (cx + 0.5)*sx - 0.5`` (half-pixel convention).
"""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation


# --------------------------------------------------------------------------- poses
def quat_pose(qx, qy, qz, qw, tx, ty, tz) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = Rotation.from_quat([qx, qy, qz, qw]).as_matrix()  # scalar-last
    T[:3, 3] = [tx, ty, tz]
    return T


def inv_T(T: np.ndarray) -> np.ndarray:
    """Inverse of one (4,4) or many (N,4,4) rigid transforms."""
    R = T[..., :3, :3]
    t = T[..., :3, 3]
    Ti = np.zeros_like(T)
    Rt = np.swapaxes(R, -1, -2)
    Ti[..., :3, :3] = Rt
    Ti[..., :3, 3] = -np.einsum("...ij,...j->...i", Rt, t)
    Ti[..., 3, 3] = 1.0
    return Ti


def rt_to_T(rvec, tvec) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = Rotation.from_rotvec(np.asarray(rvec, float).ravel()).as_matrix()
    T[:3, 3] = np.asarray(tvec, float).ravel()
    return T


def T_to_rt(T):
    return Rotation.from_matrix(T[:3, :3]).as_rotvec(), T[:3, 3].copy()


def rot_angle_deg(R1, R2) -> float:
    c = (np.trace(R1.T @ R2) - 1.0) / 2.0
    return float(np.degrees(np.arccos(np.clip(c, -1.0, 1.0))))


def transform_points(T: np.ndarray, X: np.ndarray) -> np.ndarray:
    return X @ T[:3, :3].T + T[:3, 3]


def umeyama_sim3(src: np.ndarray, dst: np.ndarray, with_scale: bool = True):
    """Least-squares similarity transform with dst ~= s * R @ src + t.  Returns (s, R, t)."""
    src = np.asarray(src, float)
    dst = np.asarray(dst, float)
    mu_s, mu_d = src.mean(0), dst.mean(0)
    xs, xd = src - mu_s, dst - mu_d
    cov = xd.T @ xs / len(src)
    U, D, Vt = np.linalg.svd(cov)
    S = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        S[2, 2] = -1
    R = U @ S @ Vt
    var_s = (xs ** 2).sum() / len(src)
    s = float(np.trace(np.diag(D) @ S) / var_s) if with_scale else 1.0
    t = mu_d - s * R @ mu_s
    return s, R, t


def sim3_matrix(s, R, t) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = s * R
    T[:3, 3] = t
    return T


def apply_sim3_to_pose(s, R, t, T_wc: np.ndarray) -> np.ndarray:
    """Map a camera-to-world pose through x' = s R x + t (rotation part stays orthonormal)."""
    out = np.eye(4)
    out[:3, :3] = R @ T_wc[:3, :3]
    out[:3, 3] = s * R @ T_wc[:3, 3] + t
    return out


# --------------------------------------------------------------------------- intrinsics
def scale_K(K: np.ndarray, sx: float, sy: float | None = None) -> np.ndarray:
    """Intrinsics after resizing an image by (sx, sy) with the half-pixel convention."""
    sy = sx if sy is None else sy
    K2 = np.array(K, dtype=np.float64, copy=True)
    K2[0, 0] *= sx
    K2[0, 1] *= sx
    K2[1, 1] *= sy
    K2[0, 2] = (K[0, 2] + 0.5) * sx - 0.5
    K2[1, 2] = (K[1, 2] + 0.5) * sy - 0.5
    return K2


def K_for_size(K: np.ndarray, src_wh, dst_wh) -> np.ndarray:
    return scale_K(K, dst_wh[0] / src_wh[0], dst_wh[1] / src_wh[1])


def project(K: np.ndarray, T_cw: np.ndarray, X: np.ndarray, dist=None):
    """Project world points. Returns (uv (N,2), z (N,)).  dist = (k1, k2) radial or None."""
    Xc = X @ T_cw[:3, :3].T + T_cw[:3, 3]
    z = Xc[:, 2]
    zs = np.where(np.abs(z) < 1e-9, 1e-9, z)
    x = Xc[:, 0] / zs
    y = Xc[:, 1] / zs
    if dist is not None and (dist[0] != 0 or dist[1] != 0):
        r2 = x * x + y * y
        f = 1 + dist[0] * r2 + dist[1] * r2 * r2
        x, y = x * f, y * f
    u = K[0, 0] * x + K[0, 1] * y + K[0, 2]
    v = K[1, 1] * y + K[1, 2]
    return np.stack([u, v], 1), z


def backproject_depth(depth: np.ndarray, K: np.ndarray, T_wc: np.ndarray, mask=None, stride: int = 1):
    """Depth map (z, metres) -> world points (N,3) plus the pixel coords used."""
    d = depth[::stride, ::stride]
    valid = np.isfinite(d) & (d > 0)
    if mask is not None:
        valid &= mask[::stride, ::stride]
    v, u = np.nonzero(valid)
    z = d[v, u].astype(np.float64)
    u = u * stride
    v = v * stride
    x = (u - K[0, 2]) / K[0, 0] * z
    y = (v - K[1, 2]) / K[1, 1] * z
    Xc = np.stack([x, y, z], 1)
    return transform_points(T_wc, Xc), np.stack([u, v], 1)


def undistort_points_norm(xd: np.ndarray, k1: float, k2: float, iters: int = 8) -> np.ndarray:
    """Invert the 2-term radial model on normalised coordinates (N,2)."""
    xu = xd.copy()
    for _ in range(iters):
        r2 = (xu ** 2).sum(1, keepdims=True)
        xu = xd / (1 + k1 * r2 + k2 * r2 * r2)
    return xu


# --------------------------------------------------------------------------- fitting
def fit_plane(P: np.ndarray, w=None):
    """Weighted least-squares plane. Returns (n, d) with n.x = d and |n| = 1."""
    if w is None:
        c = P.mean(0)
        Q = P - c
    else:
        w = np.asarray(w, float)
        c = (P * w[:, None]).sum(0) / w.sum()
        Q = (P - c) * np.sqrt(w)[:, None]
    _, _, Vt = np.linalg.svd(Q, full_matrices=False)
    n = Vt[-1]
    return n, float(n @ c)


def ransac_plane(P: np.ndarray, thresh: float, iters: int = 400, normal_hint=None,
                 max_angle_deg: float = 25.0, rng=None):
    """Robust plane fit. Optionally only accept planes whose normal is near ``normal_hint``."""
    rng = np.random.default_rng(0) if rng is None else rng
    n_pts = len(P)
    if n_pts < 3:
        raise ValueError("need >= 3 points for a plane")
    best, best_cnt = None, -1
    cos_lim = np.cos(np.radians(max_angle_deg))
    for _ in range(iters):
        idx = rng.choice(n_pts, 3, replace=False)
        a, b, c = P[idx]
        n = np.cross(b - a, c - a)
        nn = np.linalg.norm(n)
        if nn < 1e-12:
            continue
        n /= nn
        if normal_hint is not None:
            if n @ normal_hint < 0:
                n = -n
            if n @ normal_hint < cos_lim:
                continue
        d = n @ a
        cnt = int((np.abs(P @ n - d) < thresh).sum())
        if cnt > best_cnt:
            best, best_cnt = (n, d), cnt
    if best is None:
        raise RuntimeError("RANSAC found no plane")
    n, d = best
    inl = np.abs(P @ n - d) < thresh
    n, d = fit_plane(P[inl])
    if normal_hint is not None and n @ normal_hint < 0:
        n, d = -n, -d
    inl = np.abs(P @ n - d) < thresh
    return n, d, inl


def fit_circle_2d(pts: np.ndarray):
    """Algebraic (Kasa) circle fit refined by Gauss-Newton on geometric distance.
    Returns (cx, cy, r, rms_residual)."""
    pts = np.asarray(pts, float)
    x, y = pts[:, 0], pts[:, 1]
    A = np.stack([x, y, np.ones_like(x)], 1)
    b = x * x + y * y
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy = sol[0] / 2, sol[1] / 2
    r = np.sqrt(max(sol[2] + cx * cx + cy * cy, 1e-18))
    p = np.array([cx, cy, r])
    for _ in range(20):
        dx, dy = x - p[0], y - p[1]
        dist = np.sqrt(dx * dx + dy * dy) + 1e-15
        res = dist - p[2]
        J = np.stack([-dx / dist, -dy / dist, -np.ones_like(dist)], 1)
        step, *_ = np.linalg.lstsq(J, -res, rcond=None)
        p += step
        if np.abs(step).max() < 1e-12:
            break
    dx, dy = x - p[0], y - p[1]
    rms = float(np.sqrt(np.mean((np.sqrt(dx * dx + dy * dy) - p[2]) ** 2)))
    return float(p[0]), float(p[1]), float(abs(p[2])), rms


def closest_point_to_lines(origins: np.ndarray, dirs: np.ndarray) -> np.ndarray:
    """Least-squares point closest to a bundle of 3D lines (e.g. camera optical axes)."""
    dirs = dirs / np.linalg.norm(dirs, axis=1, keepdims=True)
    A = np.zeros((3, 3))
    b = np.zeros(3)
    for o, d in zip(origins, dirs):
        M = np.eye(3) - np.outer(d, d)
        A += M
        b += M @ o
    return np.linalg.lstsq(A, b, rcond=None)[0]


def min_area_rect(xy: np.ndarray):
    """Minimum-area enclosing rectangle of 2D points (rotating calipers over the hull).
    Returns dict(center, size=(long, short), angle_rad of the long side, corners)."""
    from scipy.spatial import ConvexHull

    xy = np.asarray(xy, float)
    hull = xy[ConvexHull(xy).vertices]
    edges = np.diff(np.vstack([hull, hull[:1]]), axis=0)
    angles = np.unique(np.mod(np.arctan2(edges[:, 1], edges[:, 0]), np.pi / 2))
    best = None
    for a in angles:
        c, s = np.cos(a), np.sin(a)
        R = np.array([[c, s], [-s, c]])  # rotate points by -a
        p = hull @ R.T
        mn, mx = p.min(0), p.max(0)
        area = np.prod(mx - mn)
        if best is None or area < best[0]:
            best = (area, a, mn, mx)
    _, a, mn, mx = best
    size = mx - mn
    c, s = np.cos(a), np.sin(a)
    R = np.array([[c, s], [-s, c]])
    center = R.T @ ((mn + mx) / 2)
    if size[1] > size[0]:  # make the first axis the long side
        a += np.pi / 2
        size = size[::-1]
    a = float(np.mod(a + np.pi / 2, np.pi) - np.pi / 2)  # long axis angle in (-90, 90] deg
    u = np.array([np.cos(a), np.sin(a)])
    v = np.array([-u[1], u[0]])
    h = size / 2
    corners = np.array([center + sx * h[0] * u + sy * h[1] * v for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
    return {"center": center, "size": (float(size[0]), float(size[1])), "angle": a, "corners": corners}


# --------------------------------------------------------------------------- upright rotation
def upright_k(up_cam) -> int:
    """Number of 90-degree counter-clockwise rotations (np.rot90 k) that make the image upright,
    given the world 'up' direction in camera coordinates (x right, y down)."""
    v = np.array([up_cam[0], up_cam[1]], float)
    best, best_k = -2.0, 0
    for k in range(4):
        score = -v[1] / (np.linalg.norm(v) + 1e-12)   # alignment with image-up (0, -1)
        if score > best:
            best, best_k = score, k
        v = np.array([v[1], -v[0]])                      # direction after one more CCW rotation
    return best_k


def rot_points(pts, k: int, W: int, H: int):
    """Pixel coordinates (x, y) in an image of size W x H after np.rot90(img, k)."""
    out = []
    for x, y in pts:
        w, h = W, H                      # per point: every point starts from the original size
        for _ in range(k % 4):
            x, y, w, h = y, w - 1 - x, h, w
        out.append((x, y))
    return out


def fill_triangles(img: np.ndarray, tri_px: np.ndarray, value: int = 1, shift: int = 3) -> np.ndarray:
    """Rasterise many triangles (N,3,2) as a UNION. (cv2.fillPoly with a list of polygons uses the
    even-odd rule, so overlapping front/back faces would cancel each other out.)
    tri_px are fixed-point pixel coordinates scaled by 2**shift."""
    import cv2
    tri_px = np.ascontiguousarray(tri_px, np.int32)
    for t in tri_px:
        cv2.fillConvexPoly(img, t, value, lineType=cv2.LINE_8, shift=shift)
    return img
