"""Readers for iPhone LiDAR capture apps -> one common in-memory format.

Supported exports (auto-detected from a .zip, a .r3d or a folder):
  * Stray Scanner  (free, recommended)   odometry.csv + rgb.mp4 + depth/*.png + confidence/*.png
  * Record3D       (.r3d)                metadata + rgbd/{i}.jpg/.depth/.conf (LZFSE)
  * Polycam        (Raw data export)     keyframes/{images,cameras,depth,confidence}
  * 3D Scanner App ("All Data" export)   frame_XXXXX.{jpg,json} + depth_XXXXX.png + conf_XXXXX.png

Every frame ends up with: RGB image, intrinsics K (for that RGB), camera-to-world pose in
OpenCV camera axes inside the app's ARKit world (gravity aligned, +Y up, metres), and the
LiDAR depth (float32 metres) + confidence (0/1/2) at the sensor's native 256x192.
"""
from __future__ import annotations

import glob
import io
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np
from PIL import Image

from .geometry import quat_pose, rot_angle_deg

log = logging.getLogger("partscan")
GL2CV = np.diag([1.0, -1.0, -1.0, 1.0])  # ARKit/OpenGL camera axes -> OpenCV camera axes


@dataclass
class Frame:
    idx: int                        # frame number inside the recording
    t: float                        # timestamp [s]
    K: np.ndarray                   # 3x3 intrinsics of the RGB image
    T_wc: np.ndarray                # 4x4 camera->world, OpenCV axes, ARKit world
    depth: Optional[np.ndarray] = None   # LiDAR depth [m], (h, w) float32, 0 = invalid
    conf: Optional[np.ndarray] = None    # LiDAR confidence 0/1/2 (uint8)
    sharpness: float = 0.0
    rgb_bytes: Optional[bytes] = None    # encoded image kept in memory
    rgb_path: Optional[str] = None       # or a path on disk
    rgb: Optional[np.ndarray] = None     # decoded (H, W, 3) uint8, only for keyframes
    extra: dict = field(default_factory=dict)

    def load_rgb(self) -> np.ndarray:
        if self.rgb is None:
            if self.rgb_bytes is not None:
                arr = cv2.imdecode(np.frombuffer(self.rgb_bytes, np.uint8),
                                   cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)   # K refers to stored pixels
                self.rgb = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
            elif self.rgb_path is not None:
                self.rgb = np.array(Image.open(self.rgb_path).convert("RGB"))
            else:
                raise RuntimeError(f"frame {self.idx} has no image")
        return self.rgb

    @property
    def rgb_size(self):
        if self.rgb is not None:
            return self.rgb.shape[1], self.rgb.shape[0]
        return self.extra["rgb_size"]


@dataclass
class Scan:
    frames: list
    source: str
    rgb_size: tuple
    n_total: int
    fps: float = 0.0
    notes: list = field(default_factory=list)


# --------------------------------------------------------------------------- helpers
def sharpness(gray_small: np.ndarray) -> float:
    """Variance of the Laplacian on a downscaled grayscale image (higher = sharper)."""
    return float(cv2.Laplacian(gray_small, cv2.CV_32F, ksize=3).var())


def _encode(rgb: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 96])
    assert ok
    return buf.tobytes()


def _read_png(path) -> np.ndarray:
    return np.array(Image.open(path))


def _pip_install(*pkgs):
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", *pkgs], check=True)


def _lzfse_decompress(data: bytes) -> bytes:
    try:
        import lzfse  # pip install lzfse
        return lzfse.decompress(data)
    except ImportError:
        pass
    try:
        import liblzfse  # pip install pyliblzfse
        return liblzfse.decompress(data)
    except ImportError:
        log.info("installing lzfse (needed for Record3D files)...")
        _pip_install("lzfse")
        import lzfse
        return lzfse.decompress(data)


def _window_pick(n: int, max_keep: int):
    """Split range(n) into consecutive windows so at most max_keep survive."""
    stride = max(1, math.ceil(n / max_keep))
    return stride


# --------------------------------------------------------------------------- Stray Scanner
def load_stray(root: str, max_candidates: int = 360, progress=None) -> Scan:
    import pandas as pd
    import av

    odo = pd.read_csv(os.path.join(root, "odometry.csv"), skipinitialspace=True)
    odo.columns = [c.strip() for c in odo.columns]
    n = len(odo)
    K_last = None
    cm = os.path.join(root, "camera_matrix.csv")
    if os.path.exists(cm):
        K_last = np.loadtxt(cm, delimiter=",").reshape(3, 3)
    has_k = all(c in odo.columns for c in ("fx", "fy", "cx", "cy")) and odo["fx"].notna().all()
    stride = _window_pick(n, max_candidates)
    notes = []

    frames, best = [], None
    vpath = os.path.join(root, "rgb.mp4")
    # ignore_editlist: Stray gives the first frame a negative timestamp that an edit list hides;
    # without this flag the decoder silently drops it and every image is paired with the wrong pose.
    with av.open(vpath, options={"ignore_editlist": "1"}) as cont:
        stream = cont.streams.video[0]
        stream.thread_type = "AUTO"
        W, H = stream.codec_context.width, stream.codec_context.height
        fps = float(stream.average_rate or 0) if stream.average_rate else 0.0
        n_video, extra_video = 0, False
        for k, vf in enumerate(cont.decode(stream)):
            if k >= n:
                extra_video = True  # more video frames than poses
                break
            n_video = k + 1
            small = vf.reformat(width=W // 4, height=H // 4, format="gray").to_ndarray()
            s = sharpness(small)
            if best is None or s > best[0]:
                best = (s, k, vf.to_ndarray(format="rgb24"))
            if (k + 1) % stride == 0:
                frames.append(_stray_frame(root, odo, best, has_k, K_last))
                best = None
            if progress is not None and k % 50 == 0:
                progress(k / n, f"decoding video {k}/{n}")
        if best is not None:
            frames.append(_stray_frame(root, odo, best, has_k, K_last))
    if n_video != n or extra_video:
        msg = (f"video has {'more than ' + str(n) if extra_video else n_video} frames but odometry has {n} rows;"
               f" frames were paired in order")
        log.warning(msg)
        notes.append(msg)
    if not has_k:
        notes.append("old Stray Scanner version: one intrinsics matrix for all frames (update the app to v1.4+)")
    return Scan(frames, "Stray Scanner", (W, H), n, fps, notes)


def _stray_frame(root, odo, best, has_k, K_last) -> Frame:
    s, k, rgb = best
    r = odo.iloc[k]
    fid = int(r["frame"])
    T = quat_pose(r["qx"], r["qy"], r["qz"], r["qw"], r["x"], r["y"], r["z"])  # already OpenCV axes
    if has_k:
        K = np.array([[r["fx"], 0, r["cx"]], [0, r["fy"], r["cy"]], [0, 0, 1.0]])
    else:
        K = K_last
    depth = _read_png(os.path.join(root, "depth", f"{fid:06d}.png")).astype(np.float32) / 1000.0
    cpath = os.path.join(root, "confidence", f"{fid:06d}.png")
    conf = _read_png(cpath).astype(np.uint8) if os.path.exists(cpath) else np.full(depth.shape, 2, np.uint8)
    f = Frame(fid, float(r["timestamp"]), K, T, depth, conf, s, rgb_bytes=_encode(rgb))
    f.extra["rgb_size"] = (rgb.shape[1], rgb.shape[0])
    return f


# --------------------------------------------------------------------------- Record3D
def load_r3d(path: str, max_candidates: int = 360, progress=None) -> Scan:
    if os.path.isdir(path):
        def read(name):
            with open(os.path.join(path, name), "rb") as fh:
                return fh.read()
        names = set(os.path.relpath(p, path).replace(os.sep, "/") for p in glob.glob(os.path.join(path, "**"), recursive=True))
    else:
        z = zipfile.ZipFile(path)
        read = z.read
        names = set(z.namelist())
    md = json.loads(read("metadata"))
    W, H, dw, dh = int(md["w"]), int(md["h"]), int(md["dw"]), int(md["dh"])
    K = np.array(md["K"], dtype=np.float64).reshape(3, 3).T  # stored column-major
    poses = md["poses"]
    ts = md.get("frameTimestamps") or [i / float(md.get("fps", 30)) for i in range(len(poses))]
    n = len(poses)
    stride = _window_pick(n, max_candidates)
    frames = []
    for w0 in range(0, n, stride):
        best = None
        for i in range(w0, min(n, w0 + stride)):
            name = f"rgbd/{i}.jpg"
            if name not in names:
                continue
            im = Image.open(io.BytesIO(read(name)))
            im.draft("L", (im.width // 4, im.height // 4))
            s = sharpness(np.array(im.convert("L")))
            if best is None or s > best[0]:
                best = (s, i)
        if best is None:
            continue
        s, i = best
        raw = read(f"rgbd/{i}.jpg")
        depth = np.frombuffer(_lzfse_decompress(read(f"rgbd/{i}.depth")), np.float32).reshape(dh, dw).copy()
        depth[~np.isfinite(depth)] = 0
        cname = f"rgbd/{i}.conf"
        conf = (np.frombuffer(_lzfse_decompress(read(cname)), np.uint8).reshape(dh, dw).copy()
                if cname in names else np.full((dh, dw), 2, np.uint8))
        f = Frame(i, float(ts[i]) if i < len(ts) else float(i), K.copy(), quat_pose(*poses[i]) @ GL2CV,
                  depth, conf, s, rgb_bytes=raw)
        f.extra["rgb_size"] = (W, H)
        frames.append(f)
        if progress is not None:
            progress(w0 / n, f"reading Record3D frame {i}/{n}")
    notes = []
    if W < 1000:
        notes.append(f"Record3D stored only {W}x{H} RGB - enable the highest RGB quality in the app for better detail")
    return Scan(frames, "Record3D", (W, H), n, float(md.get("fps", 0)), notes)


# --------------------------------------------------------------------------- Polycam
def load_polycam(root: str, max_candidates: int = 360, progress=None) -> Scan:
    kf = os.path.join(root, "keyframes")
    # original images + their intrinsics (the depth maps belong to these); loop-closed poses if available
    corrected = os.path.isdir(os.path.join(kf, "corrected_cameras"))
    cam_dir = os.path.join(kf, "cameras") if os.path.isdir(os.path.join(kf, "cameras")) else os.path.join(kf, "corrected_cameras")
    pose_dir = os.path.join(kf, "corrected_cameras") if corrected else cam_dir
    img_dir = os.path.join(kf, "images") if os.path.isdir(os.path.join(kf, "images")) else os.path.join(kf, "corrected_images")
    files = sorted(glob.glob(os.path.join(cam_dir, "*.json")), key=lambda p: int(os.path.basename(p)[:-5]))
    stride = _window_pick(len(files), max_candidates)
    frames, W, H = [], None, None
    for k in range(0, len(files), stride):
        jf = files[k]
        ts = int(os.path.basename(jf)[:-5])
        c = json.load(open(jf))
        pj = os.path.join(pose_dir, os.path.basename(jf))
        cp = json.load(open(pj)) if os.path.exists(pj) else c
        T = np.eye(4)
        T[:3, :] = [[cp[f"t_{r}{q}"] for q in range(4)] for r in range(3)]
        K = np.array([[c["fx"], 0, c["cx"]], [0, c["fy"], c["cy"]], [0, 0, 1.0]])
        W, H = int(c["width"]), int(c["height"])
        dpath = os.path.join(kf, "depth", f"{ts}.png")
        cpath = os.path.join(kf, "confidence", f"{ts}.png")
        if not os.path.exists(dpath):
            continue
        depth = _read_png(dpath).astype(np.float32) / 1000.0
        conf = (_read_png(cpath) // 127).astype(np.uint8) if os.path.exists(cpath) else np.full(depth.shape, 2, np.uint8)
        ipath = os.path.join(img_dir, f"{ts}.jpg")
        im = Image.open(ipath)
        im.draft("L", (im.width // 4, im.height // 4))
        f = Frame(ts, ts * 1e-6, K, T @ GL2CV, depth, conf, sharpness(np.array(im.convert("L"))), rgb_path=ipath)
        f.extra["rgb_size"] = (W, H)
        frames.append(f)
    notes = ["Polycam keyframes" + (" with loop-closed (corrected) poses" if corrected else "")]
    return Scan(frames, "Polycam (raw data)", (W, H), len(files), 0.0, notes)


# --------------------------------------------------------------------------- 3D Scanner App
def load_3dscannerapp(root: str, max_candidates: int = 360, progress=None) -> Scan:
    jsons = sorted(glob.glob(os.path.join(root, "frame_*.json")))
    with_img = [j for j in jsons if os.path.exists(j[:-5] + ".jpg")]
    stride = _window_pick(len(with_img), max_candidates)
    frames, W, H = [], None, None
    for jf in with_img[::stride]:
        i = int(os.path.basename(jf)[6:11])
        d = json.load(open(jf))
        T_gl = np.array(d["cameraPoseARFrame"], dtype=np.float64).reshape(4, 4)
        K = np.array(d["intrinsics"], dtype=np.float64).reshape(3, 3)
        jpg = jf[:-5] + ".jpg"
        im = Image.open(jpg)
        W, H = im.size
        im.draft("L", (im.width // 4, im.height // 4))
        depth = _read_png(os.path.join(root, f"depth_{i:05d}.png")).astype(np.float32) / 1000.0
        cp = os.path.join(root, f"conf_{i:05d}.png")
        conf = _read_png(cp).astype(np.uint8) if os.path.exists(cp) else np.full(depth.shape, 2, np.uint8)
        f = Frame(i, float(d.get("time", i)), K, T_gl @ GL2CV, depth, conf,
                  sharpness(np.array(im.convert("L"))), rgb_path=jpg)
        f.extra["rgb_size"] = (W, H)
        frames.append(f)
    return Scan(frames, "3D Scanner App", (W, H), len(jsons), 0.0, [])


# --------------------------------------------------------------------------- auto-detect
def _find(root: str, test, max_depth: int = 4):
    root = os.path.abspath(root)
    base_depth = root.rstrip(os.sep).count(os.sep)
    for d, dirs, files in os.walk(root):
        if d.count(os.sep) - base_depth > max_depth:
            dirs[:] = []
            continue
        dirs[:] = sorted(x for x in dirs if not x.startswith(("__MACOSX", ".")))
        if test(d, set(files), set(dirs)):
            return d
    return None


def load_scan(path: str, work_dir: str, max_candidates: int = 360, progress=None) -> Scan:
    """Load any supported capture (zip / .r3d / folder)."""
    path = os.path.abspath(path)
    if os.path.isfile(path) and path.lower().endswith(".r3d"):
        return load_r3d(path, max_candidates, progress)
    if os.path.isfile(path) and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            if "metadata" in z.namelist() and any(n.startswith("rgbd/") for n in z.namelist()):
                return load_r3d(path, max_candidates, progress)
        out = os.path.join(work_dir, "extracted")
        shutil.rmtree(out, ignore_errors=True)
        os.makedirs(out, exist_ok=True)
        with zipfile.ZipFile(path) as z:
            z.extractall(out)
        path = out
    if not os.path.isdir(path):
        raise ValueError(f"Unsupported input: {path}. Upload the zip exported by Stray Scanner, "
                         "a Record3D .r3d, a Polycam raw-data zip, or a 3D Scanner App 'All Data' zip.")
    tests = [
        ("stray", lambda d, f, s: "odometry.csv" in f and "rgb.mp4" in f),
        ("r3d", lambda d, f, s: "metadata" in f and "rgbd" in s),
        ("polycam", lambda d, f, s: "keyframes" in s and (
            os.path.isdir(os.path.join(d, "keyframes", "cameras")) or
            os.path.isdir(os.path.join(d, "keyframes", "corrected_cameras")))),
        ("3dsa", lambda d, f, s: any(x.startswith("frame_") and x.endswith(".json") for x in f)),
    ]
    for kind, test in tests:
        d = _find(path, test)
        if d is None:
            continue
        log.info("detected %s capture in %s", kind, d)
        if kind == "stray":
            return load_stray(d, max_candidates, progress)
        if kind == "r3d":
            return load_r3d(d, max_candidates, progress)
        if kind == "polycam":
            return load_polycam(d, max_candidates, progress)
        return load_3dscannerapp(d, max_candidates, progress)
    raise ValueError("Could not recognise the capture format. Expected a Stray Scanner folder "
                     "(odometry.csv + rgb.mp4), Record3D .r3d, Polycam raw data or 3D Scanner App 'All Data'.")


# --------------------------------------------------------------------------- keyframes
def select_keyframes(frames: list, n: int, min_sharpness_ratio: float = 0.35) -> list:
    """Pick ~n sharp frames spread evenly along the camera path (by rotation + translation)."""
    if not frames:
        return []
    frames = sorted(frames, key=lambda f: f.t)
    s = np.array([f.sharpness for f in frames])
    keep = s >= min_sharpness_ratio * np.median(s)
    cand = [f for f, k in zip(frames, keep) if k]
    if len(cand) <= n:
        return cand
    motion = [0.0]
    for a, b in zip(cand[:-1], cand[1:]):
        dr = rot_angle_deg(a.T_wc[:3, :3], b.T_wc[:3, :3]) / 5.0          # 5 deg  ~ 1 unit
        dt = np.linalg.norm(a.T_wc[:3, 3] - b.T_wc[:3, 3]) / 0.02          # 2 cm   ~ 1 unit
        motion.append(motion[-1] + dr + dt)
    motion = np.array(motion)
    edges = np.linspace(0, motion[-1] + 1e-9, n + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        idx = np.nonzero((motion >= lo) & (motion < hi))[0]
        if len(idx):
            out.append(cand[int(idx[np.argmax([cand[i].sharpness for i in idx])])])
    return out
