"""Volumetric fusion: TSDF of the corrected depth maps intersected with a robust visual hull.

    solid = TSDF-solid  AND  visual-hull-solid  AND  {z >= 0 (support plane)}
    sdf   = max(sdf_tsdf, sdf_hull, -z)

* TSDF: part and background pixels are integrated (the background proves free space); mixed
  pixels at depth edges only contribute free space in front of the near surface. Voxels with too
  little evidence (the inside of the part) count as solid.
* Visual hull: for every view, the distance of a voxel outside the silhouette is
  (pixels outside the mask - tolerance) * depth / focal. The hull SDF is the k-th largest of
  those values, so a single bad mask can never cut into the part.
* Support plane: the underside the camera never saw is closed exactly at z = 0.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

log = logging.getLogger("partscan")


@dataclass
class FusionFrame:
    T_cw: np.ndarray                 # 4x4 world(part frame)->camera
    depth: Optional[np.ndarray]      # (h,w) metres, 0 = invalid
    Kd: Optional[np.ndarray]         # intrinsics of the depth map
    depth_mask: Optional[np.ndarray]  # (h,w) bool: pixels allowed into the TSDF
    sdt: Optional[np.ndarray] = None  # (H,W) float32 signed distance to silhouette [px], >0 outside
    K: Optional[np.ndarray] = None    # intrinsics of the silhouette image
    weight: float = 1.0
    weight_map: Optional[np.ndarray] = None  # (h,w) per-pixel confidence for the TSDF
    free_depth: Optional[np.ndarray] = None  # (h,w) for excluded (edge) pixels: space closer than this is empty


def auto_voxel(roi_min, roi_max, voxel_mm: float = 0.0) -> float:
    if voxel_mm and voxel_mm > 0:
        return voxel_mm / 1000.0
    ext = float(np.max(np.asarray(roi_max) - np.asarray(roi_min)))
    return float(np.clip(ext / 320.0, 0.0002, 0.001))


def fuse(frames, roi_min, roi_max, voxel: float, trunc: float, k_carve: int = 3, tol_px: float = 1.0,
         close_bottom: bool = True, device: str = "cpu", progress=None, chunk: int | None = None,
         min_weight: float = 0.8, min_views: int = 2, trunc_back: float | None = None):
    """trunc: band in front of a surface; trunc_back: (shorter) band behind it, so that views of the far
    side of a thin wall do not thicken it. min_views / min_weight: evidence needed before a voxel counts
    as observed - one wrong depth map can then never hollow out the part."""
    if trunc_back is None:
        trunc_back = trunc
    import torch

    roi_min = np.asarray(roi_min, float)
    roi_max = np.asarray(roi_max, float)
    dims = np.maximum(np.ceil((roi_max - roi_min) / voxel).astype(int), 2)
    N = int(np.prod(dims))
    dx, dy, dz = (int(v) for v in dims)
    if chunk is None:
        chunk = 6_000_000 if device.startswith("cuda") else 1_500_000
    dev = torch.device(device)
    f32 = torch.float32
    # move frames to the device once; torch.from_numpy shares memory (no CPU copy), and frames may be a
    # generator so the caller never needs to hold every frame's buffers at the same time
    def T(a, dtype=None):
        t = torch.from_numpy(np.ascontiguousarray(a if dtype is None else np.asarray(a, dtype)))
        return t.to(dev) if dev.type != "cpu" else t

    up = []
    for fr in frames:
        e = {"R": torch.tensor(fr.T_cw[:3, :3], dtype=f32, device=dev),
             "t": torch.tensor(fr.T_cw[:3, 3], dtype=f32, device=dev), "w": fr.weight}
        if fr.depth is not None:
            h, w = fr.depth.shape
            e.update(D=T(fr.depth, np.float32).reshape(-1), M=T(fr.depth_mask, np.bool_).reshape(-1),
                     Wm=T(fr.weight_map, np.float16).reshape(-1) if fr.weight_map is not None else None,
                     Fd=T(fr.free_depth, np.float32).reshape(-1) if fr.free_depth is not None else None,
                     Kd=[float(fr.Kd[0, 0]), float(fr.Kd[1, 1]), float(fr.Kd[0, 2]), float(fr.Kd[1, 2])], hw=(h, w))
        if fr.sdt is not None:
            H, W = fr.sdt.shape
            e.update(S=T(fr.sdt, np.float16)[None, None],
                     K=[float(fr.K[0, 0]), float(fr.K[1, 1]), float(fr.K[0, 2]), float(fr.K[1, 2])], HW=(H, W))
        up.append(e)
    n_sil = sum("S" in e for e in up)
    k_eff = int(min(k_carve, max(1, n_sil)))
    out = np.empty(N, np.float32)
    seen = np.zeros(N, np.uint8)
    o = torch.tensor(roi_min, dtype=f32, device=dev)
    neg_inf = torch.tensor(float("-inf"), device=dev)
    for c0 in range(0, N, chunk):
        idx = torch.arange(c0, min(N, c0 + chunk), device=dev)
        iz = idx % dz
        iy = (idx // dz) % dy
        ix = idx // (dy * dz)
        P = o + (torch.stack([ix, iy, iz], 1).to(f32) + 0.5) * voxel
        n = len(idx)
        tsdf = torch.ones(n, dtype=f32, device=dev)
        wsum = torch.zeros(n, dtype=f32, device=dev)
        nobs = torch.zeros(n, dtype=torch.int16, device=dev)
        vh = torch.full((k_eff, n), float("-inf"), dtype=f32, device=dev) if n_sil else None
        for e in up:
            Xc = P @ e["R"].T + e["t"]
            z = Xc[:, 2]
            front = z > 0.03
            zs = torch.where(front, z, torch.ones_like(z))
            xn, yn = Xc[:, 0] / zs, Xc[:, 1] / zs
            if "D" in e:
                fx, fy, cx, cy = e["Kd"]
                h, w = e["hw"]
                u = torch.round(fx * xn + cx).long()
                v = torch.round(fy * yn + cy).long()
                ins = front & (u >= 0) & (u < w) & (v >= 0) & (v < h)
                lin = v.clamp(0, h - 1) * w + u.clamp(0, w - 1)
                d = e["D"][lin]
                ok = ins & e["M"][lin] & (d > 0)
                sdf = (d - z) * torch.sqrt(1 + xn * xn + yn * yn)
                upd = ok & (sdf > -trunc_back)
                obs = torch.clamp(sdf / trunc, -1.0, 1.0)
                wo = upd.to(f32) * e["w"]
                if e["Wm"] is not None:
                    wo = wo * e["Wm"][lin].to(f32)
                if e["Fd"] is not None:
                    # pixels excluded at depth edges: the NEAREST neighbouring depth is certainly a
                    # surface or in front of one, so space in front of it is observed as outside
                    # (never as inside - behind it could be either side of the edge)
                    fd = e["Fd"][lin]
                    sdf_f = (fd - z) * torch.sqrt(1 + xn * xn + yn * yn)
                    fupd = ins & ~e["M"][lin] & (fd > 0) & (sdf_f > 0)
                    obs = torch.where(fupd, torch.clamp(sdf_f / trunc, 0.0, 1.0), obs)
                    wo = torch.where(fupd, torch.full_like(wo, 0.5 * e["w"]), wo)
                    upd = upd | fupd
                tsdf = torch.where(upd, (tsdf * wsum + obs * wo) / (wsum + wo).clamp_min(1e-6), tsdf)
                wsum = wsum + wo
                nobs = nobs + (upd & (wo > 0)).to(torch.int16)
            if "S" in e:
                fx, fy, cx, cy = e["K"]
                H, W = e["HW"]
                u2 = fx * xn + cx
                v2 = fy * yn + cy
                ins2 = front & (u2 >= 0) & (u2 <= W - 1) & (v2 >= 0) & (v2 <= H - 1)
                grid = torch.stack([u2 / (W - 1) * 2 - 1, v2 / (H - 1) * 2 - 1], 1)[None, None]
                s = torch.nn.functional.grid_sample(e["S"].to(f32), grid, mode="bilinear", padding_mode="border",
                                                    align_corners=True)[0, 0, 0]
                val = torch.where(ins2, (s - tol_px) * z / fx, neg_inf)
                vh = torch.topk(torch.cat([vh, val[None]], 0), k_eff, dim=0).values
        # a voxel counts as observed only with ~one frontal view's worth of evidence: a single biased
        # frame can then never hollow out the inside of the part
        observed = (wsum >= min_weight) & (nobs >= min_views)
        sdf_t = torch.where(observed, tsdf * trunc, torch.full_like(tsdf, -trunc))
        sdf = sdf_t
        if vh is not None:
            sdf = torch.maximum(sdf, vh[k_eff - 1])
        if close_bottom:
            sdf = torch.maximum(sdf, -P[:, 2])
        out[c0:c0 + n] = torch.clamp(sdf, -trunc, trunc).cpu().numpy()
        seen[c0:c0 + n] = observed.cpu().numpy()
        if progress is not None:
            progress(min(1.0, (c0 + n) / N), f"fusing volume {100 * (c0 + n) // N}%")
    vol = out.reshape(dx, dy, dz)
    n_void = fill_enclosed_voids(vol, trunc)
    stats = dict(dims=[dx, dy, dz], voxel_mm=voxel * 1000, observed_fraction=float(seen.mean()),
                 silhouette_views=n_sil, carve_k=k_eff, voids_filled=int(n_void))
    return vol, stats


def fill_enclosed_voids(vol: np.ndarray, trunc: float) -> int:
    """Empty pockets that do not connect to the outside of the volume cannot be seen by any camera:
    they are noise, so make them solid (in place). Holes, slots and pockets stay open because they
    connect to the outside. Returns the number of voxels filled."""
    from scipy import ndimage
    lab, n = ndimage.label(vol > 0)
    if n <= 1:
        return 0
    border = np.unique(np.concatenate([lab[0].ravel(), lab[-1].ravel(), lab[:, 0].ravel(), lab[:, -1].ravel(),
                                       lab[:, :, 0].ravel(), lab[:, :, -1].ravel()]))
    enclosed = np.ones(n + 1, bool)
    enclosed[0] = False
    enclosed[border] = False
    fill = enclosed[lab]
    vol[fill] = -trunc
    return int(fill.sum())


def extract_mesh(vol: np.ndarray, roi_min, voxel: float):
    """Marching cubes at sdf = 0 -> (vertices [m, part frame], faces) with outward normals."""
    from skimage.measure import marching_cubes
    import trimesh

    if not (vol.min() < 0 < vol.max()):
        raise RuntimeError("The fused volume contains no surface - the part was not reconstructed. "
                           "Check that the part is visible in the scan and the board/support plane was found.")
    v, f, _, _ = marching_cubes(vol, level=0.0, spacing=(voxel, voxel, voxel), allow_degenerate=False)
    v = v + np.asarray(roi_min) + 0.5 * voxel
    m = trimesh.Trimesh(v, f, process=True)
    if m.volume < 0:
        m.invert()
    return m


def sample_sdf(vol, roi_min, voxel, pts):
    """Trilinear SDF lookup at world points (NaN outside the grid)."""
    from scipy.ndimage import map_coordinates
    g = (np.asarray(pts) - np.asarray(roi_min)) / voxel - 0.5
    val = map_coordinates(vol, g.T, order=1, mode="constant", cval=np.nan)
    return val
