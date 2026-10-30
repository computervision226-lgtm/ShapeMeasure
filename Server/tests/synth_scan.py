"""Synthetic iPhone-LiDAR scans of a known manufacturing part, written in the exact
Stray Scanner v1.4 export format, plus a hidden ground-truth file for oracle tests.

What is simulated
  * an L-bracket (60 x 40 x 30 mm, wall 6 mm, base 8 mm, 2x dia 8 through holes, dia 6 cross hole)
    lying rotated on a printed ChArUco board on a wooden table
  * handheld two-ring orbit, portrait hold, jitter
  * camera: fx~1450 @1920x1440 with focus breathing, radial lens distortion (not reported, like ARKit)
  * ARKit poses: 1.2% scale error, random-walk drift in rotation and translation, jitter
  * LiDAR 256x192: spatial blur (big laser footprint), per-frame bias, smooth + white noise,
    confidence from depth edges
  * rgb.mp4 with the app's first-frame negative timestamp quirk

Usage:  python synth_scan.py OUT_DIR [--res 1920] [--fps 15] [--seconds 40] [--no-board]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from fractions import Fraction

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cv2  # noqa: E402
import open3d as o3d  # noqa: E402
import trimesh  # noqa: E402
from PIL import Image  # noqa: E402
from scipy.ndimage import gaussian_filter, map_coordinates  # noqa: E402
from scipy.spatial.transform import Rotation  # noqa: E402

from partscan.board import board_to_part, make_board  # noqa: E402
from partscan.config import BoardSpec  # noqa: E402

MM = 1e-3


# --------------------------------------------------------------------------- part
def make_bracket():
    """L-bracket in its own frame: base on z=0, centred in x/y. Units: metres."""
    base = trimesh.creation.box((60 * MM, 40 * MM, 8 * MM))
    base.apply_translation((0, 0, 4 * MM))
    wall = trimesh.creation.box((6 * MM, 40 * MM, 22 * MM))
    wall.apply_translation((-27 * MM, 0, 19 * MM))
    body = trimesh.boolean.union([base, wall])
    holes = []
    for y in (-10, 10):
        c = trimesh.creation.cylinder(radius=4 * MM, height=20 * MM, sections=128)
        c.apply_translation((12 * MM, y * MM, 4 * MM))
        holes.append(c)
    xh = trimesh.creation.cylinder(radius=3 * MM, height=20 * MM, sections=128)
    xh.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, (0, 1, 0)))
    xh.apply_translation((-27 * MM, 0, 20 * MM))
    holes.append(xh)
    part = trimesh.boolean.difference([body] + holes)
    gt = {"length_mm": 60.0, "width_mm": 40.0, "height_mm": 30.0, "wall_thickness_mm": 6.0,
          "base_thickness_mm": 8.0, "holes": [{"d_mm": 8.0, "axis": "z", "center_mm": [12, -10]},
                                              {"d_mm": 8.0, "axis": "z", "center_mm": [12, 10]},
                                              {"d_mm": 6.0, "axis": "x", "center_mm": [0, 20]}],
          "volume_mm3": float(part.volume / MM ** 3)}
    return part, gt


# --------------------------------------------------------------------------- textures
class Textures:
    def __init__(self, spec: BoardSpec | None, rng):
        self.spec = spec
        self.ppm = 12  # board texture px per mm
        if spec is not None:
            b = make_board(spec)
            self.board_img = b.generateImage((int(spec.squares_x * spec.square_mm * self.ppm),
                                              int(spec.squares_y * spec.square_mm * self.ppm)),
                                             marginSize=0, borderBits=1).astype(np.float32) / 255.0
            self.T_pb = board_to_part(spec)
        self.wood = gaussian_filter(rng.random((512, 512)), (1.0, 12.0)).astype(np.float32)
        self.wood = (self.wood - self.wood.mean()) / (self.wood.std() + 1e-9)
        self.paper = gaussian_filter(rng.random((512, 512)), 2.0).astype(np.float32)
        self.paper = (self.paper - self.paper.mean()) / (self.paper.std() + 1e-9)
        self.speck = rng.random((64, 64, 64)).astype(np.float32)
        self.part_rgb = np.array([0.62, 0.63, 0.66], np.float32)

    def plane_albedo(self, x, y):
        out = np.empty((len(x), 3), np.float32)
        # wood table
        wx = (x / 0.25 * 512) % 512
        wy = (y / 0.25 * 512) % 512
        g = map_coordinates(self.wood, [wy, wx], order=1, mode="wrap")
        base = np.array([0.55, 0.38, 0.22], np.float32)
        out[:] = base * (1 + 0.18 * g[:, None]) + 0.06 * np.sin(x * 900 + 2.5 * g)[:, None]
        if self.spec is None:
            return np.clip(out, 0, 1)
        on_paper = (np.abs(x) < 0.1485) & (np.abs(y) < 0.105)
        pg = map_coordinates(self.paper, [(y * 4000) % 512, (x * 4000) % 512], order=1, mode="wrap")
        out[on_paper] = (0.9 + 0.012 * pg[on_paper])[:, None]
        # board squares: invert board_to_part (x = bx - cx, y = cy - by)
        cx = self.spec.squares_x * self.spec.square_mm / 2000
        cy = self.spec.squares_y * self.spec.square_mm / 2000
        bx = (x + cx) * 1000 * self.ppm - 0.5
        by = (cy - y) * 1000 * self.ppm - 0.5
        h, w = self.board_img.shape
        inb = (bx >= 0) & (bx <= w - 1) & (by >= 0) & (by <= h - 1)
        val = map_coordinates(self.board_img, [by[inb], bx[inb]], order=1)
        out[inb] = (0.06 + 0.86 * val)[:, None]
        return np.clip(out, 0, 1)

    def part_albedo(self, P):
        q = (P / 0.0007) % 64
        s = map_coordinates(self.speck, [q[:, 0], q[:, 1], q[:, 2]], order=1, mode="wrap")
        return np.clip(self.part_rgb[None] * (0.88 + 0.24 * s[:, None]), 0, 1)


# --------------------------------------------------------------------------- scene / renderer
class Scene:
    def __init__(self, part_world: trimesh.Trimesh, tex: Textures):
        self.part = part_world
        self.tex = tex
        self.sc = o3d.t.geometry.RaycastingScene()
        pm = o3d.t.geometry.TriangleMesh()
        pm.vertex.positions = o3d.core.Tensor(np.asarray(part_world.vertices, np.float32))
        pm.triangle.indices = o3d.core.Tensor(np.asarray(part_world.faces, np.int32))
        self.part_id = self.sc.add_triangles(pm)
        S = 6.0
        plane = o3d.t.geometry.TriangleMesh()
        plane.vertex.positions = o3d.core.Tensor(np.array([[-S, -S, 0], [S, -S, 0], [S, S, 0], [-S, S, 0]], np.float32))
        plane.triangle.indices = o3d.core.Tensor(np.array([[0, 1, 2], [0, 2, 3]], np.int32))
        self.plane_id = self.sc.add_triangles(plane)
        self.light = np.array([0.35, -0.45, 0.82])
        self.light /= np.linalg.norm(self.light)

    def rays(self, K, T_wc, W, H, dist):
        u, v = np.meshgrid(np.arange(W, dtype=np.float64), np.arange(H, dtype=np.float64))
        xd = (u - K[0, 2]) / K[0, 0]
        yd = (v - K[1, 2]) / K[1, 1]
        xu, yu = xd.copy(), yd.copy()
        if dist is not None:
            for _ in range(10):  # invert radial distortion
                r2 = xu * xu + yu * yu
                f = 1 + dist[0] * r2 + dist[1] * r2 * r2
                xu, yu = xd / f, yd / f
        d_cam = np.stack([xu, yu, np.ones_like(xu)], -1).reshape(-1, 3)
        d_w = d_cam @ T_wc[:3, :3].T
        o = np.broadcast_to(T_wc[:3, 3], d_w.shape)
        return np.concatenate([o, d_w], 1).astype(np.float32), d_cam

    def render(self, K, T_wc, W, H, dist=None, shade=True):
        r, d_cam = self.rays(K, T_wc, W, H, dist)
        ans = self.sc.cast_rays(o3d.core.Tensor(r))
        t = ans["t_hit"].numpy()
        gid = ans["geometry_ids"].numpy()
        hit = np.isfinite(t)
        z = np.where(hit, t * d_cam[:, 2], 0.0).astype(np.float32)   # t is in units of |d| with d_cam.z = 1
        obj = np.where(hit, np.where(gid == self.part_id, 1, 2), 0).astype(np.uint8)
        out = {"depth": z.reshape(H, W), "obj": obj.reshape(H, W)}
        if shade:
            P = r[:, :3] + r[:, 3:] * np.where(hit, t, 0)[:, None]
            n = ans["primitive_normals"].numpy()
            col = np.zeros((len(t), 3), np.float32)
            m_part = obj == 1
            m_pl = obj == 2
            if m_pl.any():
                col[m_pl] = self.tex.plane_albedo(P[m_pl, 0], P[m_pl, 1])
            if m_part.any():
                col[m_part] = self.tex.part_albedo(P[m_part])
            nd = np.abs(n @ self.light)
            # simple view-dependent sheen on the part (machined metal look)
            vdir = -r[:, 3:] / np.linalg.norm(r[:, 3:], axis=1, keepdims=True)
            hvec = vdir + self.light
            hvec /= np.linalg.norm(hvec, axis=1, keepdims=True)
            spec = np.where(m_part, np.abs((n * hvec).sum(1)) ** 40 * 0.25, 0)
            shade_ = 0.38 + 0.62 * nd
            img = col * shade_[:, None] + spec[:, None]
            # cast shadows (one shadow ray per pixel hitting the plane)
            if m_pl.any():
                sr = np.concatenate([P[m_pl] + self.light * 1e-4, np.broadcast_to(self.light, (m_pl.sum(), 3))], 1)
                occ = self.sc.test_occlusions(o3d.core.Tensor(sr.astype(np.float32))).numpy()
                img[np.nonzero(m_pl)[0][occ]] *= 0.55
            out["rgb"] = np.clip(img.reshape(H, W, 3), 0, 1)
        return out


# --------------------------------------------------------------------------- trajectory
def look_at(eye, target, up=np.array([0, 0, 1.0]), roll=0.0):
    z = target - eye
    z /= np.linalg.norm(z)
    x = np.cross(z, up)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    R = np.stack([x, y, z], 1)
    R = R @ Rotation.from_rotvec([0, 0, roll]).as_matrix()
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = eye
    return T


def trajectory(n, center, rng, side=None):
    """Guided capture: three rings (low / mid / high) and a slow pass over the top, handheld jitter.
    side = dict(center=, axis=): add short passes looking straight into a side hole from both ends."""
    Ts = []
    ph = rng.random(8) * 6.28
    plan = [(18, 0.30, 0.34), (45, 0.30, 0.33), (72, 0.20, 0.33), (88, 0.20, 0.32)]   # elevation, share, radius
    if side is not None:
        plan += [("side+", 0.08, 0.30), ("side-", 0.08, 0.30)]
    shares = np.array([p[1] for p in plan])
    shares /= shares.sum()
    bounds = np.r_[0, np.cumsum(shares)]
    for k in range(n):
        a = k / max(1, n - 1)
        ring = int(np.clip(np.searchsorted(bounds, a, side="right") - 1, 0, len(plan) - 1))
        aa = (a - bounds[ring]) / shares[ring]
        el_deg, _, rad = plan[ring]
        if isinstance(el_deg, str):   # look straight into the side hole, phone held low (2-10 deg)
            ax_ = side["axis"] * (1 if el_deg == "side+" else -1)
            az = np.arctan2(ax_[1], ax_[0]) + np.radians(20) * np.sin(2 * np.pi * aa + ph[7])
            el = np.radians(6 + 4 * np.sin(3 * np.pi * aa + ph[0]))
            rad = rad + 0.012 * np.sin(3 * np.pi * aa + ph[1])
            eye = side["center"] + rad * np.array([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)])
            tgt = side["center"] + 0.003 * np.array([np.sin(4 * a * 6.28 + ph[5]), np.cos(3 * a * 6.28), 0])
            roll = -np.pi / 2 + 0.08 * np.sin(4 * a * 6.28 + ph[1])
            Ts.append(look_at(eye, tgt, up=np.array([0, 0, 1.0]), roll=roll))
            continue
        if ring == 3:   # top pass: sweep across the top instead of orbiting
            az = 2 * np.pi * 0.35 * aa + ph[6]
            el = np.radians(80 + 9 * np.sin(np.pi * aa))
        else:
            az = 2 * np.pi * aa * (1 if ring % 2 == 0 else -1) + 0.4 * ring
            el = np.radians(el_deg) + 0.05 * np.sin(5 * np.pi * aa + ph[0])
        rad = rad + 0.012 * np.sin(3 * np.pi * aa + ph[1])
        eye = center + rad * np.array([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)])
        eye += 0.004 * np.array([np.sin(7 * a * 6.28 + ph[2]), np.sin(5 * a * 6.28 + ph[3]), np.sin(6 * a * 6.28 + ph[4])])
        tgt = center + 0.006 * np.array([np.sin(4 * a * 6.28 + ph[5]), np.cos(3 * a * 6.28), 0])
        up = np.array([0, 0, 1.0]) if el < np.radians(84) else np.array([np.cos(az + np.pi), np.sin(az + np.pi), 0])
        roll = -np.pi / 2 + 0.08 * np.sin(4 * a * 6.28 + ph[1])  # portrait hold
        Ts.append(look_at(eye, tgt, up=up, roll=roll))
    return np.array(Ts)


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--res", type=int, default=1920, help="RGB width (1920 = real ARKit)")
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--seconds", type=float, default=40)
    ap.add_argument("--no-board", action="store_true")
    ap.add_argument("--k1", type=float, default=-0.012)
    ap.add_argument("--k2", type=float, default=0.004)
    ap.add_argument("--arkit-scale", type=float, default=1.012)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--side-views", action="store_true", help="add passes looking straight into the cross hole")
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    os.makedirs(a.out, exist_ok=True)
    scan_dir = os.path.join(a.out, "scan")
    os.makedirs(os.path.join(scan_dir, "depth"), exist_ok=True)
    os.makedirs(os.path.join(scan_dir, "confidence"), exist_ok=True)

    spec = None if a.no_board else BoardSpec()
    part_local, gt = make_bracket()
    yaw = np.radians(17.0)
    T_part = trimesh.transformations.rotation_matrix(yaw, (0, 0, 1))
    T_part[:3, 3] = [6 * MM, -9 * MM, 0]
    part_world = part_local.copy()
    part_world.apply_transform(T_part)
    tex = Textures(spec, rng)
    scene = Scene(part_world, tex)

    W = a.res
    H = W * 3 // 4
    s = W / 1920.0
    n = int(a.seconds * a.fps)
    center = np.array([6 * MM, -9 * MM, 12 * MM])
    side = None
    if a.side_views:
        hc = T_part @ np.array([-27 * MM, 0, 20 * MM, 1.0])
        side = dict(center=hc[:3], axis=T_part[:3, :3] @ np.array([1.0, 0, 0]))
    T_true = trajectory(n, center, rng, side)

    # intrinsics with focus breathing (reported per frame, like Stray v1.4)
    Ks = []
    for k in range(n):
        f = 1450.0 * (1 + 0.0012 * np.sin(k / n * 9.0))
        K = np.array([[f * s, 0, (965.3 + 0.5) * s - 0.5], [0, f * s, (721.8 + 0.5) * s - 0.5], [0, 0, 1]])
        Ks.append(K)
    Ks = np.array(Ks)
    dist = (a.k1, a.k2)

    # ARKit world: +Y up, origin at the first camera, arbitrary yaw
    R_ap = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float)
    R_ap = Rotation.from_rotvec([0, np.radians(33), 0]).as_matrix() @ R_ap
    T_ap = np.eye(4)
    T_ap[:3, :3] = R_ap
    T_ap[:3, 3] = -R_ap @ T_true[0][:3, 3]
    # ARKit error model
    drift_t = np.cumsum(rng.normal(0, 0.00012, (n, 3)), 0)
    drift_r = np.cumsum(rng.normal(0, np.radians(0.008), (n, 3)), 0)
    T_ark = []
    for k in range(n):
        Tw = T_ap @ T_true[k]
        Tw[:3, 3] *= a.arkit_scale
        Tw[:3, 3] += drift_t[k] + rng.normal(0, 0.00015, 3)
        Tw[:3, :3] = Rotation.from_rotvec(drift_r[k]).as_matrix() @ Tw[:3, :3]
        T_ark.append(Tw)
    T_ark = np.array(T_ark)

    # odometry.csv (Stray v1.4 header, quaternion scalar-last, OpenCV camera axes)
    with open(os.path.join(scan_dir, "odometry.csv"), "w") as fh:
        fh.write("timestamp, frame, x, y, z, qx, qy, qz, qw, fx, fy, cx, cy, distortion_center_x, distortion_center_y\n")
        for k in range(n):
            q = Rotation.from_matrix(T_ark[k][:3, :3]).as_quat()
            x, y, z = T_ark[k][:3, 3]
            K = Ks[k]
            fh.write(f"{1000.0 + k / a.fps:.6f}, {k:06d}, {x:.7f}, {y:.7f}, {z:.7f}, {q[0]:.8f}, {q[1]:.8f}, "
                     f"{q[2]:.8f}, {q[3]:.8f}, {K[0, 0]:.4f}, {K[1, 1]:.4f}, {K[0, 2]:.4f}, {K[1, 2]:.4f}, , \n")
    np.savetxt(os.path.join(scan_dir, "camera_matrix.csv"), Ks[-1], delimiter=",", fmt="%.6f")
    with open(os.path.join(scan_dir, "imu.csv"), "w") as fh:
        fh.write("timestamp, a_x, a_y, a_z, alpha_x, alpha_y, alpha_z\n")

    # video
    import av
    cont = av.open(os.path.join(scan_dir, "rgb.mp4"), "w")
    st = cont.add_stream("libx264", rate=a.fps)
    st.width, st.height, st.pix_fmt = W, H, "yuv420p"
    st.time_base = Fraction(1, a.fps)
    st.codec_context.time_base = Fraction(1, a.fps)
    st.options = {"crf": "17", "bf": "0", "preset": "veryfast"}
    dw, dh = 256, 192
    for k in range(n):
        out = scene.render(Ks[k], T_true[k], W, H, dist)
        img = out["rgb"]
        img = np.clip(img + rng.normal(0, 0.008, img.shape), 0, 1)
        img8 = (np.power(img, 1 / 2.2) * 255 + 0.5).astype(np.uint8)
        fr = av.VideoFrame.from_ndarray(img8, format="rgb24")
        fr.pts = k - 1                   # Stray quirk: first frame at -1
        fr.time_base = Fraction(1, a.fps)
        for p in st.encode(fr):
            cont.mux(p)
        # LiDAR
        Kd = Ks[k].copy()
        sx = dw / W
        Kd[0, 0] *= sx
        Kd[1, 1] *= sx
        Kd[0, 2] = (Ks[k][0, 2] + 0.5) * sx - 0.5
        Kd[1, 2] = (Ks[k][1, 2] + 0.5) * sx - 0.5
        lo = scene.render(Kd, T_true[k], dw, dh, dist, shade=False)
        d = lo["depth"].astype(np.float64)
        d = np.where(d > 0, d, d[d > 0].max() if (d > 0).any() else 1.0)
        d_blur = gaussian_filter(d, 1.6)                                   # coarse laser footprint
        field = gaussian_filter(rng.normal(0, 1, (dh // 8, dw // 8)), 1.0)
        field = cv2.resize(field, (dw, dh), interpolation=cv2.INTER_CUBIC) * 0.0025
        bias = 0.002 + rng.normal(0, 0.0015)
        sc = 1 + rng.normal(0, 0.004)
        dn = d_blur * sc + bias + field + rng.normal(0, 0.0012, d.shape)
        gy, gx = np.gradient(d)
        gm = np.hypot(gx, gy)
        conf = np.full((dh, dw), 2, np.uint8)
        conf[gm > 0.004] = 1
        conf[gm > 0.015] = 0
        Image.fromarray(np.clip(dn * 1000, 0, 65535).astype(np.uint16)).save(os.path.join(scan_dir, "depth", f"{k:06d}.png"))
        Image.fromarray(conf).save(os.path.join(scan_dir, "confidence", f"{k:06d}.png"))
        if k % 50 == 0:
            print(f"frame {k}/{n}", flush=True)
    for p in st.encode():
        cont.mux(p)
    cont.close()

    np.savez_compressed(os.path.join(a.out, "truth.npz"),
                        T_wc_part=T_true, Ks=Ks, dist=np.array(dist), T_arkit_from_part=T_ap,
                        part_vertices=np.asarray(part_world.vertices), part_faces=np.asarray(part_world.faces),
                        part_local_vertices=np.asarray(part_local.vertices),
                        T_part_pose=T_part, W=W, H=H, has_board=spec is not None)
    with open(os.path.join(a.out, "truth.json"), "w") as fh:
        json.dump({**gt, "arkit_scale": a.arkit_scale, "k1": a.k1, "k2": a.k2, "frames": n, "fps": a.fps,
                   "res": [W, H], "board": spec is not None}, fh, indent=1)
    # zip the scan folder like the app's share sheet does
    import shutil
    shutil.make_archive(os.path.join(a.out, "stray_scan"), "zip", a.out, "scan")
    print("done:", a.out)


if __name__ == "__main__":
    main()
