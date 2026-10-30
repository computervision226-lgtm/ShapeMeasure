"""Measurements on the final mesh (canonical frame, millimetres).

* overall size as placed: length (X) x width (Y) x height (Z) of the footprint rectangle
* minimum-volume oriented bounding box (any orientation)
* volume, surface area, mass for a chosen material, centre of mass
* holes / round bosses: circles fitted to cross-sections along X, Y and Z, merged across levels
* planar faces: wall thicknesses (opposite faces) and step heights (same-facing faces)
"""
from __future__ import annotations

import logging

import numpy as np
import trimesh

from .config import MATERIAL_DENSITY_G_CM3
from .geometry import fit_circle_2d, fit_plane

log = logging.getLogger("partscan")
AXES = {"X": np.array([1.0, 0, 0]), "Y": np.array([0, 1.0, 0]), "Z": np.array([0, 0, 1.0])}


def overall(m: trimesh.Trimesh) -> dict:
    lo, hi = m.bounds
    ext = hi - lo
    out = dict(length_x=float(ext[0]), width_y=float(ext[1]), height_z=float(hi[2]),
               bbox_min=lo.tolist(), bbox_max=hi.tolist())
    try:
        T, e = trimesh.bounds.oriented_bounds(m)
        e = np.sort(np.asarray(e))[::-1]
        out["obb_extents"] = e.tolist()
    except Exception:
        out["obb_extents"] = sorted(ext.tolist(), reverse=True)
    return out


def mass_props(m: trimesh.Trimesh, material: str, sdf_volume_mm3=None) -> dict:
    wt = bool(m.is_watertight)
    vol = float(abs(m.volume)) if wt else float(sdf_volume_mm3 if sdf_volume_mm3 is not None else "nan")
    dens = MATERIAL_DENSITY_G_CM3.get(material, 0.0)
    ok = vol == vol
    return dict(volume_mm3=vol, area_mm2=float(m.area), watertight=wt,
                mass_g=(vol / 1000.0 * dens) if (ok and dens) else float("nan"),
                material=material, density_g_cm3=dens,
                center_of_mass=(m.center_mass.tolist() if wt else m.centroid.tolist()))


# --------------------------------------------------------------------------- circles
def _section_polys(m, origin, normal):
    try:
        sec = m.section(plane_origin=origin, plane_normal=normal)
    except Exception:
        return [], None
    if sec is None:
        return [], None
    try:
        p2, T = sec.to_2D()
        return list(p2.polygons_full), T
    except Exception:
        return [], None


def _ring_harmonics(pts, cx, cy, r, nb=72):
    """Shape of a ring's radial deviation: (largest share of it in ONE harmonic 3..8, 2nd harmonic / r).
    A polygon (square, hexagon...) concentrates its deviation in one harmonic; a rough scanned bore doesn't."""
    th = np.arctan2(pts[:, 1] - cy, pts[:, 0] - cx)
    e = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy) - r
    b = ((th + np.pi) / (2 * np.pi) * nb).astype(int) % nb
    s, c = np.bincount(b, e, nb), np.bincount(b, None, nb)
    ok = c > 0
    prof = np.interp(np.arange(nb), np.flatnonzero(ok), s[ok] / c[ok], period=nb)
    A = np.abs(np.fft.rfft(prof - prof.mean())) / nb * 2
    var = max((A[1:] ** 2).sum() / 2, 1e-12)
    return float((A[3:9] ** 2 / 2).max() / var), float(A[2] / r)


def _circle_from_ring(coords, min_r=0.4, allow_rough=False):
    """(cx, cy, r, rms, rough) for a closed, round ring, else None. 'rough' = round opening whose
    outline is too ragged for a precise diameter (e.g. a side hole seen only at an angle)."""
    pts = np.asarray(coords)[:-1]
    if len(pts) < 10:
        return None
    cx, cy, r, rms = fit_circle_2d(pts)
    if r < min_r:
        return None
    ang = np.sort(np.arctan2(pts[:, 1] - cy, pts[:, 0] - cx))
    gap = np.max(np.diff(np.r_[ang, ang[0] + 2 * np.pi]))
    # scanned surfaces are noisy: accept up to 0.12 mm or 4% of r RMS deviation, no big gaps,
    # and the ring must close (area of the ring ~ area of the circle)
    from shapely.geometry import Polygon
    try:
        area_ratio = abs(Polygon(pts).area) / (np.pi * r * r)
    except Exception:
        area_ratio = 0
    if gap > np.radians(45) or not (0.9 < area_ratio < 1.1):
        return None
    if rms <= max(0.12, 0.04 * r):
        return cx, cy, r, rms, False
    if allow_rough and rms <= min(0.4, 0.13 * r):
        poly, ell = _ring_harmonics(pts, cx, cy, r)
        if poly < 0.6 and ell < 0.2:          # not a polygon (hex socket, square pocket) nor a long slot
            return cx, cy, r, rms, True
    return None


def find_circles(m: trimesh.Trimesh, step_mm: float = 0.6, max_levels: int = 260, work_faces: int = 150000) -> list:
    """Holes (interior rings) and round outer features on sections along each axis."""
    if len(m.faces) > work_faces * 1.3:
        try:
            m = m.simplify_quadric_decimation(face_count=work_faces)
        except Exception:
            pass
    found = []
    lo, hi = m.bounds
    for name, ax in AXES.items():
        k = "XYZ".index(name)
        span = hi[k] - lo[k]
        if span < 1.0:
            continue
        n_levels = int(np.clip(span / step_mm, 6, max_levels))
        levels = np.linspace(lo[k] + 0.02 * span, hi[k] - 0.02 * span, n_levels)
        for lev in levels:
            origin = np.zeros(3)
            origin[k] = lev
            polys, T = _section_polys(m, origin, ax)
            for poly in polys:
                rings = [("hole", r) for r in poly.interiors] + [("outer", poly.exterior)]
                for kind, ring in rings:
                    c = _circle_from_ring(ring.coords, allow_rough=(kind == "hole"))
                    if c is None:
                        continue
                    cx, cy, r, rms, rough = c
                    p3 = (T @ np.array([cx, cy, 0, 1.0]))[:3]
                    found.append(dict(kind=kind, axis=name, level=float(lev), center=p3, d=2 * r, rms=rms,
                                      rough=rough))
    # merge detections of the same feature across levels
    feats = []
    for det in found:
        k = "XYZ".index(det["axis"])
        perp = [i for i in range(3) if i != k]
        for fe in feats:
            if fe["kind"] == det["kind"] and fe["axis"] == det["axis"] and \
                    np.linalg.norm(fe["center"][perp] - det["center"][perp]) < max(0.6, 0.12 * det["d"]) and \
                    abs(fe["d"] - det["d"]) < max(0.6, 0.15 * det["d"]):
                fe["ds"].append(det["d"])
                fe["levels"].append(det["level"])
                fe["centers"].append(det["center"])
                fe["rough"].append(det["rough"])
                fe["center"] = np.mean(fe["centers"], 0)
                fe["d"] = float(np.median(fe["ds"]))
                break
        else:
            feats.append(dict(kind=det["kind"], axis=det["axis"], center=det["center"].copy(), d=det["d"],
                              ds=[det["d"]], levels=[det["level"]], centers=[det["center"]], rough=[det["rough"]]))
    out = []
    for fe in feats:
        if len(fe["ds"]) < 3:
            continue  # seen on too few slices: likely a coincidence
        k = "XYZ".index(fe["axis"])
        rough = np.array(fe["rough"])
        lv_all = np.array(fe["levels"])
        # a clean bore: measure it on its clean slices only. Otherwise report it, flagged as rough
        use = ~rough if (~rough).sum() >= 3 else np.ones(len(rough), bool)
        is_rough = bool(rough[use].any())
        lv = lv_all[use]
        ds = np.array(fe["ds"])[use][np.argsort(lv)]
        lv = np.sort(lv)
        step = (hi[k] - lo[k]) * 0.96 / max(1, int(np.clip((hi[k] - lo[k]) / step_mm, 6, max_levels)) - 1)
        # rims are rounded by the scan; the middle of the bore is the reliable part
        core = ds[len(ds) // 5: len(ds) - len(ds) // 5] if len(ds) >= 5 else ds
        out.append(dict(kind=fe["kind"], axis=fe["axis"], diameter=float(np.median(core)),
                        diameter_spread=float(np.ptp(ds)), center=[float(c) for c in fe["center"]],
                        span=[float(lv_all.min()), float(lv_all.max())],
                        length=float(lv_all.max() - lv_all.min() + step), n_slices=len(ds), rough=is_rough))
    out.sort(key=lambda d: (d["kind"] != "hole", d.get("rough", False), -d["diameter"]))
    return out


# --------------------------------------------------------------------------- planes
def _sphere_grid(K=8000):
    i = np.arange(K) + 0.5
    phi = np.arccos(1 - 2 * i / K)
    th = np.pi * (1 + 5 ** 0.5) * i
    return np.stack([np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)], 1)


def find_planes(m: trimesh.Trimesh, angle_deg: float = 8.0, dist_mm: float = 0.4, min_area_frac: float = 0.006,
                max_planes: int = 30, work_faces: int = 25000, smooth_mm: float = 2.0) -> list:
    """Planar faces of a (noisy, scanned) mesh.
    1. decimate (averages out triangle-level noise), 2. dominant normal directions on a sphere
    grid, 3. peaks of the offset histogram per direction, 4. refit each plane on the full mesh."""
    from scipy.ndimage import gaussian_filter1d
    from scipy.sparse import csr_matrix
    from scipy.spatial import cKDTree

    ms = m
    if len(m.faces) > work_faces * 1.5:
        try:
            ms = m.simplify_quadric_decimation(face_count=work_faces)
        except Exception:
            ms = m
    n0, a, c = ms.face_normals, ms.area_faces, ms.triangles_center
    total = a.sum()
    # scanned faces are bumpy at the ~1 mm scale: average normals over a 2 mm neighbourhood
    tc = cKDTree(c)
    prs = tc.query_pairs(r=smooth_mm, output_type="ndarray")
    Adj = csr_matrix((np.ones(2 * len(prs)), (np.r_[prs[:, 0], prs[:, 1]], np.r_[prs[:, 1], prs[:, 0]])),
                     shape=(len(c), len(c)))
    acc = n0 * a[:, None]
    acc = acc + Adj @ acc
    n = acc / np.maximum(np.linalg.norm(acc, axis=1, keepdims=True), 1e-12)
    G = _sphere_grid()
    tree = cKDTree(G)
    _, idx = tree.query(n)
    hist = np.bincount(idx, a, minlength=len(G))
    pairs = tree.query_pairs(r=2 * np.sin(np.radians(5) / 2), output_type="ndarray")
    A = csr_matrix((np.ones(2 * len(pairs)), (np.r_[pairs[:, 0], pairs[:, 1]], np.r_[pairs[:, 1], pairs[:, 0]])),
                   shape=(len(G), len(G)))
    sm = hist + A @ hist
    cos_t = np.cos(np.radians(angle_deg))
    dirs = []
    for j in np.argsort(-sm):
        if sm[j] < min_area_frac * total:
            break
        if any(G[j] @ d > np.cos(np.radians(1.5 * angle_deg)) for d in dirs):
            continue
        sel = n @ G[j] > cos_t
        d = (n[sel] * a[sel, None]).sum(0)
        dirs.append(d / np.linalg.norm(d))
    # full-resolution data for the refit
    Vf, Nf = m.vertices, m.vertex_normals
    planes = []
    used = np.zeros(len(n), bool)
    for d in dirs:
        sel = (n @ d > cos_t) & ~used
        if a[sel].sum() < min_area_frac * total:
            continue
        off = c[sel] @ d
        bw = 0.05
        lo = off.min() - 1
        nb_ = int((off.max() + 1 - lo) / bw) + 1
        hs = gaussian_filter1d(np.bincount(((off - lo) / bw).astype(int), a[sel], minlength=nb_), 2.0)
        cand = sorted((k for k in range(1, nb_ - 1) if hs[k] >= hs[k - 1] and hs[k] >= hs[k + 1]), key=lambda k: -hs[k])
        for k in cand:
            o0 = lo + (k + 0.5) * bw
            mem = sel & ~used & (np.abs(c @ d - o0) < dist_mm)
            if a[mem].sum() < min_area_frac * total:
                continue
            nn, dd = fit_plane(c[mem], a[mem])
            if nn @ d < 0:
                nn, dd = -nn, -dd
            mem = sel & ~used & (np.abs(c @ nn - dd) < dist_mm)
            if a[mem].sum() < min_area_frac * total:
                continue
            used |= mem
            # refit on full-resolution vertices close to the plane with roughly matching normals
            vm = np.zeros(len(Vf), bool)
            for _ in range(3):
                vm = (np.abs(Vf @ nn - dd) < 0.6 * dist_mm) & (Nf @ nn > np.cos(np.radians(12)))
                if vm.sum() <= 30:
                    break
                nn2, dd2 = fit_plane(Vf[vm])
                if nn2 @ nn < 0:
                    nn2, dd2 = -nn2, -dd2
                if nn2 @ nn < 0.999:
                    break
                nn, dd = nn2, dd2
            if vm.sum() > 30:
                res = Vf[vm] @ nn - dd
            else:
                res = c[mem] @ nn - dd
            planes.append(dict(normal=nn, offset=float(dd), area=float(a[mem].sum()),
                               flatness_rms=float(np.sqrt(np.mean(res ** 2))), pts=c[mem]))
            if len(planes) >= max_planes:
                break
    planes.sort(key=lambda p: -p["area"])
    return planes


def _plane_separation(p, q) -> float:
    """Distance between two (nearly) parallel planes, measured where the faces actually are.
    Faces of similar size: mean distance of each face's points to the other plane (robust to small tilts).
    Otherwise the larger face gives the better-defined plane (a narrow face's fitted tilt is uncertain, and a
    tilt error grows with distance): mean distance of the smaller face's points to the larger face's plane."""
    if max(p["area"], q["area"]) >= 1.5 * min(p["area"], q["area"]):
        big, small = (p, q) if p["area"] >= q["area"] else (q, p)
        return float(np.abs(small["pts"] @ big["normal"] - big["offset"]).mean())
    dpq = np.abs(p["pts"] @ q["normal"] - q["offset"]).mean()
    dqp = np.abs(q["pts"] @ p["normal"] - p["offset"]).mean()
    return float(0.5 * (dpq + dqp))


def plane_relations(planes: list, max_items: int = 12) -> list:
    rel = []
    for i in range(len(planes)):
        for j in range(i + 1, len(planes)):
            p, q = planes[i], planes[j]
            cd = float(p["normal"] @ q["normal"])
            if cd < -0.995:      # opposite faces -> thickness / width
                kind = "thickness"
            elif cd > 0.995:     # same facing -> step
                kind = "step"
            else:
                continue
            dist = _plane_separation(p, q)
            if dist < 0.3:
                continue
            # the two faces must overlap when projected along the normal (otherwise unrelated)
            nrm = p["normal"]
            basis = np.linalg.svd(np.eye(3) - np.outer(nrm, nrm))[0][:, :2]
            pa, qa = p["pts"] @ basis, q["pts"] @ basis
            ov = np.all(np.minimum(pa.max(0), qa.max(0)) - np.maximum(pa.min(0), qa.min(0)) > -1.0)
            if kind == "thickness" and not ov:
                continue
            rel.append(dict(kind=kind, distance=dist, planes=(i, j), weight=min(p["area"], q["area"]),
                            normal=[float(v) for v in nrm]))
    rel.sort(key=lambda r: -r["weight"])
    return rel[:max_items]


def face_dims(planes: list, ov: dict, total_area: float, mesh=None) -> dict:
    """Length/width/height between opposite planar faces (noise-free alternative to the envelope).
    Only reported for a pair of large opposite faces that are the part's outermost ones: spanning (almost)
    the whole envelope, or - when thin noise spikes inflate the envelope - with (almost) nothing of the part
    lying beyond either face."""
    out = {}
    ext = {"X": ov["length_x"], "Y": ov["width_y"], "Z": ov["height_z"]}
    if mesh is not None:
        tc, ta = mesh.triangles_center, mesh.area_faces
    for k, ax in enumerate("XYZ"):
        e = np.eye(3)[k]
        pos = [p for p in planes if p["normal"] @ e > 0.998 and p["area"] > 0.004 * total_area]
        neg = [p for p in planes if p["normal"] @ e < -0.998 and p["area"] > 0.004 * total_area]
        best = None
        for p in pos:
            for q in neg:
                d = _plane_separation(p, q)
                if d > ext[ax] + 0.3:
                    continue
                ok = d >= ext[ax] - 0.8
                if not ok and mesh is not None and d >= ext[ax] - max(3.0, 0.02 * ext[ax]):
                    beyond = ta[(tc @ p["normal"] - p["offset"] > 0.5) | (tc @ q["normal"] - q["offset"] > 0.5)]
                    ok = beyond.sum() < 0.001 * ta.sum()
                if ok and (best is None or d > best[0]):
                    best = (d, min(p["area"], q["area"]))
        if best is not None:
            out[ax] = float(best[0])
    return out


def best_dims(meas: dict):
    """(L, W, H, how) - between-faces values where available, else the envelope."""
    o = meas["overall"]
    bf = o.get("between_faces", {})
    L = bf.get("X", o["length_x"])
    W = bf.get("Y", o["width_y"])
    H = bf.get("Z", o["height_z"])
    how = "between faces" if len(bf) == 3 else ("envelope" if not bf else "faces/envelope")
    return float(L), float(W), float(H), how


def axis_label(nrm) -> str:
    k = int(np.argmax(np.abs(nrm)))
    if abs(nrm[k]) > 0.97:
        return "XYZ"[k]
    return "oblique"


def measure_all(m_mm: trimesh.Trimesh, material: str = "Steel", sdf_volume_mm3=None) -> dict:
    res = dict(overall=overall(m_mm), mass=mass_props(m_mm, material, sdf_volume_mm3))
    try:
        res["circles"] = find_circles(m_mm)
    except Exception as ex:
        log.warning("circle detection failed: %s", ex)
        res["circles"] = []
    try:
        planes = find_planes(m_mm)
        res["planes"] = [dict(normal=[float(v) for v in p["normal"]], offset=p["offset"], area=p["area"],
                              flatness_rms=p["flatness_rms"], axis=axis_label(p["normal"])) for p in planes]
        res["plane_relations"] = plane_relations(planes)
        res["overall"]["between_faces"] = face_dims(planes, res["overall"], float(m_mm.area), m_mm)
    except Exception as ex:
        log.warning("plane detection failed: %s", ex)
        res["planes"], res["plane_relations"] = [], []
    return res
