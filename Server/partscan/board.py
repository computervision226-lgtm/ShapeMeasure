"""Printable ChArUco calibration board: generation, detection and per-frame pose.

Putting the part on this board gives three things LiDAR cannot: an exact metric scale
(from the printed square size you measure), drift-free camera poses, and a perfectly
known support plane.
"""
from __future__ import annotations

import logging

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .config import BoardSpec

log = logging.getLogger("partscan")

PAPER_MM = {"A4": (297.0, 210.0), "Letter": (279.4, 215.9), "A3": (420.0, 297.0), "Tabloid": (431.8, 279.4)}


def make_board(spec: BoardSpec):
    d = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, spec.dictionary))
    return cv2.aruco.CharucoBoard((spec.squares_x, spec.squares_y), spec.square_mm / 1000.0,
                                  spec.marker_mm / 1000.0, d)


def make_detector(board):
    dp = cv2.aruco.DetectorParameters()
    dp.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    dp.adaptiveThreshWinSizeMax = 53
    cp = cv2.aruco.CharucoParameters()
    cp.tryRefineMarkers = True
    return cv2.aruco.CharucoDetector(board, cp, dp)


def board_to_part(spec: BoardSpec) -> np.ndarray:
    """4x4 transform from OpenCV board coordinates (origin at a corner, y down the print,
    z into the table) to the PartScan world: origin at the board centre, z up."""
    cx = spec.squares_x * spec.square_mm / 2000.0
    cy = spec.squares_y * spec.square_mm / 2000.0
    T = np.diag([1.0, -1.0, -1.0, 1.0])
    T[:3, 3] = [-cx, cy, 0.0]
    return T


def board_extent_part(spec: BoardSpec):
    """(xmin, xmax, ymin, ymax) of the printed squares in the part frame [m]."""
    hx = spec.squares_x * spec.square_mm / 2000.0
    hy = spec.squares_y * spec.square_mm / 2000.0
    return -hx, hx, -hy, hy


def detect(gray: np.ndarray, board, detector):
    """Returns (img_pts (N,2) float64, ids (N,), obj_pts (N,3) float64) or None."""
    cc, ci, _, _ = detector.detectBoard(gray)
    if ci is None or len(ci) < 4:
        return None
    obj, img = board.matchImagePoints(cc, ci)
    return img.reshape(-1, 2).astype(np.float64), ci.ravel().astype(int), obj.reshape(-1, 3).astype(np.float64)


def pnp(obj: np.ndarray, img: np.ndarray, K: np.ndarray, dist=None):
    """Board pose T_cb (board -> camera) and reprojection RMS [px]."""
    dist = np.zeros(5) if dist is None else np.asarray(dist, float)
    ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist, flags=cv2.SOLVEPNP_IPPE)
    if not ok:
        ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist, flags=cv2.SOLVEPNP_ITERATIVE)
        if not ok:
            return None, np.inf
    rvec, tvec = cv2.solvePnPRefineLM(obj, img, K, dist, rvec, tvec)
    proj, _ = cv2.projectPoints(obj, rvec, tvec, K, dist)
    rms = float(np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - img) ** 2, 1))))
    T = np.eye(4)
    T[:3, :3] = cv2.Rodrigues(rvec)[0]
    T[:3, 3] = tvec.ravel()
    return T, rms


def robust_pnp(obj: np.ndarray, img: np.ndarray, K: np.ndarray, dist=None, min_pts: int = 8):
    """PnP with iterative rejection of mis-detected corners (e.g. partly hidden by the part).
    Returns (T_cb, keep_mask, rms_px) or (None, None, inf)."""
    keep = np.ones(len(img), bool)
    T, rms = None, np.inf
    for _ in range(4):
        if keep.sum() < min_pts:
            return None, None, np.inf
        T, rms = pnp(obj[keep], img[keep], K, dist)
        if T is None:
            return None, None, np.inf
        Xc = obj @ T[:3, :3].T + T[:3, 3]
        uv = Xc[:, :2] / Xc[:, 2:3] * [K[0, 0], K[1, 1]] + [K[0, 2], K[1, 2]]
        err = np.linalg.norm(uv - img, axis=1)
        thr = max(0.8, 3.0 * 1.4826 * np.median(np.abs(err[keep])))
        new_keep = err < thr
        if (new_keep == keep).all():
            break
        keep = new_keep
    return T, keep, rms


# --------------------------------------------------------------------------- printable PDF
def _font(px: int):
    try:
        import matplotlib
        import os
        return ImageFont.truetype(os.path.join(matplotlib.get_data_path(), "fonts", "ttf", "DejaVuSans.ttf"), px)
    except Exception:
        return ImageFont.load_default()


def render_board_pdf(spec: BoardSpec, path: str, paper: str = "A4", px_per_mm: int = 24) -> str:
    """Write a print-ready PDF (landscape) with the board at exact physical size plus
    reference rulers. px_per_mm=24 -> 609.6 dpi so every square is an integer number of pixels."""
    pw, ph = PAPER_MM[paper]
    W, H = int(round(pw * px_per_mm)), int(round(ph * px_per_mm))
    bw_mm, bh_mm = spec.squares_x * spec.square_mm, spec.squares_y * spec.square_mm
    if bw_mm > pw - 16 or bh_mm > ph - 16:
        raise ValueError(f"{bw_mm:.0f}x{bh_mm:.0f} mm board does not fit on {paper}; use smaller squares or bigger paper")
    page = Image.new("L", (W, H), 255)
    board = make_board(spec)
    bw, bh = int(round(bw_mm * px_per_mm)), int(round(bh_mm * px_per_mm))
    img = board.generateImage((bw, bh), marginSize=0, borderBits=1)
    x0 = int(round((pw - bw_mm) / 2 * px_per_mm))
    y0 = int(round((ph - bh_mm) / 2 * px_per_mm))
    page.paste(Image.fromarray(img), (x0, y0))
    dr = ImageDraw.Draw(page)
    mm = px_per_mm
    f_small = _font(int(2.6 * mm))
    top = y0 / mm  # mm of margin above the board
    txt = (f"PartScan ChArUco board  |  {spec.squares_x}x{spec.squares_y} squares of {spec.square_mm:g} mm, "
           f"markers {spec.marker_mm:g} mm, {spec.dictionary}  |  PRINT AT 100% / ACTUAL SIZE, glue flat to a rigid board")
    dr.text((x0, max(1.5, top / 2 - 1.5) * mm), txt, fill=0, font=f_small)
    # reference ruler under the board: 100 mm with 10 mm ticks
    by = y0 + bh + int(min(4, (ph - bh_mm) / 2 - 6) * mm)
    L = 100 * mm
    dr.line([(x0, by), (x0 + L, by)], fill=0, width=max(2, mm // 6))
    for k in range(11):
        tick = int((2.5 if k % 5 == 0 else 1.5) * mm)
        dr.line([(x0 + k * 10 * mm, by - tick), (x0 + k * 10 * mm, by)], fill=0, width=max(2, mm // 6))
    dr.text((x0 + L + 2 * mm, by - 3 * mm),
            f"100 mm check ruler: measure it (or 6 squares between inner corners = {6 * spec.square_mm:g} mm) "
            f"and enter the value in the app.", fill=0, font=f_small)
    page.save(path, "PDF", resolution=px_per_mm * 25.4)
    return path


def render_board_png(spec: BoardSpec, path: str, px_per_mm: int = 10) -> str:
    board = make_board(spec)
    img = board.generateImage((int(spec.squares_x * spec.square_mm * px_per_mm),
                               int(spec.squares_y * spec.square_mm * px_per_mm)), marginSize=0, borderBits=1)
    Image.fromarray(img).save(path)
    return path
