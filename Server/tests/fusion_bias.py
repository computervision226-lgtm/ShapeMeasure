"""Isolate fusion biases: true poses, rendered depth/masks with switchable error sources.

python fusion_bias.py SYNTH_DIR [n_frames] [scale]
Prints, per variant, the fitted positions of the part's main faces vs the truth (part frame, mm).
"""
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import trimesh  # noqa: E402
from scipy.ndimage import gaussian_filter  # noqa: E402

from oracles import _Truth  # noqa: E402
from partscan import pipeline as pl  # noqa: E402
from partscan import segment as segmod  # noqa: E402
from partscan.fusion import FusionFrame, extract_mesh, fuse  # noqa: E402
from partscan.geometry import K_for_size, inv_T  # noqa: E402
from partscan.meshproc import clean_mesh  # noqa: E402

SYN = sys.argv[1]
NF = int(sys.argv[2]) if len(sys.argv) > 2 else 40
SC = float(sys.argv[3]) if len(sys.argv) > 3 else 0.7
tr = _Truth(os.path.join(SYN, "truth.npz"))
t = np.load(os.path.join(SYN, "truth.npz"))
T_part = t["T_part_pose"]
W, H = int(t["W"]), int(t["H"])
idx = np.linspace(0, len(tr.T) - 1, NF).astype(int)


class F:  # minimal frame stand-in for the oracle renderer (true pinhole camera, no distortion)
    def __init__(self, i):
        self.idx = int(i)
        self.K = tr.Ks[i]
        self.extra = {}
        self.rgb_size = (W, H)


tr.dist = np.array([0.0, 0.0])
frames = [F(i) for i in idx]
w, h = int(round(W * SC / 14) * 14), int(round(H * SC / 14) * 14)
data = []
t0 = time.time()
for f in frames:
    z, _ = tr.render(f, w, h)
    _, part = tr.render(f, W, H)
    data.append((z, part))
print(f"rendered {len(frames)} frames in {time.time() - t0:.0f}s at depth {w}x{h}")

faces = {"x+30 (base end)": (np.array([1, 0, 0.]), 30.0), "x-30 (wall back)": (np.array([-1, 0, 0.]), 30.0),
         "x-24 (wall inner)": (np.array([1, 0, 0.]), -24.0), "y+20": (np.array([0, 1, 0.]), 20.0),
         "y-20": (np.array([0, -1, 0.]), 20.0), "z8 (base top)": (np.array([0, 0, 1.]), 8.0),
         "z30 (wall top)": (np.array([0, 0, 1.]), 30.0)}


def face_offsets(mesh_mm_local):
    V, N = mesh_mm_local.vertices, mesh_mm_local.vertex_normals
    out = {}
    for name, (n, d) in faces.items():
        sel = (np.abs(V @ n - d) < 0.8) & (N @ n > 0.9)
        # stay away from edges: only the central part of each face
        if sel.sum() < 30:
            out[name] = float("nan")
            continue
        out[name] = float(np.median(V[sel] @ n) - d)
    return out


def run(name, blur, use_vh, noise=0.0):
    fr = []
    rng = np.random.default_rng(0)
    for f, (z, part) in zip(frames, data):
        d = z.astype(np.float64)
        bg = d > 0
        d = np.where(bg, d, d[bg].max())
        if blur:
            d = gaussian_filter(d, blur)
        if noise:
            d = d + rng.normal(0, noise, d.shape)
        D = d.astype(np.float32)
        Kd = K_for_size(f.K, (W, H), (w, h))
        mk = ~pl._depth_edges(D)
        mr = cv2.resize(part.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST)
        mk &= ~pl._occluding_contours(D, mr, max(2, int(round(4 * w / 1920))))
        free = pl._edge_free_depth(D, mk)
        sdt = segmod.mask_sdt(part) if use_vh else None
        fr.append(FusionFrame(inv_T(tr.T[f.idx]), D, Kd, mk, sdt, f.K, weight_map=pl._angle_weight(D, Kd),
                              free_depth=free))
    lo = T_part[:3, 3] + np.array([-0.045, -0.045, -0.002])
    hi = T_part[:3, 3] + np.array([0.045, 0.045, 0.036])
    vox = 0.0003
    t1 = time.time()
    vol, st = fuse(fr, lo, hi, vox, trunc=0.0012, k_carve=3, tol_px=1.0, close_bottom=True, device="cpu")
    from scipy import ndimage
    lab, nl = ndimage.label(vol > 0)
    border = set(np.unique(np.concatenate([lab[0].ravel(), lab[-1].ravel(), lab[:, 0].ravel(), lab[:, -1].ravel(),
                                           lab[:, :, 0].ravel(), lab[:, :, -1].ravel()])))
    cav = [k for k in range(1, nl + 1) if k not in border]
    sizes = np.bincount(lab.ravel())[cav] if cav else []
    if len(cav):
        big = cav[int(np.argmax(sizes))]
        c = (np.argwhere(lab == big).mean(0) + 0.5) * vox + lo
        c_local = np.linalg.inv(T_part)[:3, :3] @ c + np.linalg.inv(T_part)[:3, 3]
        print(f"   cavities: {len(cav)}  voxels {int(np.sum(sizes))}  largest {int(np.max(sizes))} at local mm {np.round(c_local * 1000, 1)}")
    else:
        print("   cavities: 0")
    globals()["_last"] = (vol, lo, vox, fr)
    m = clean_mesh(extract_mesh(vol, lo, vox), smooth_iterations=8)
    m.apply_transform(np.linalg.inv(T_part))
    m.apply_scale(1000)
    off = face_offsets(m)
    ext = m.bounds[1] - m.bounds[0]
    print(f"{name:34s} {time.time() - t1:4.0f}s  extent {ext[0]:.2f} x {ext[1]:.2f} x {m.bounds[1][2]:.2f}  |  " +
          "  ".join(f"{k.split()[0]}:{v:+.3f}" for k, v in off.items()), flush=True)


for args in [("A perfect depth + VH", 0, True), ("B blur1.2 + VH", 1.2, True), ("C perfect depth, no VH", 0, False),
             ("D blur1.2, no VH", 1.2, False), ("E blur1.2+noise0.4 + VH", 1.2, True, 0.0004)]:
    run(*args)
