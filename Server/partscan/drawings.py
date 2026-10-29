"""Dimensioned orthographic drawings and QA images (matplotlib, no GPU / OpenGL needed)."""
from __future__ import annotations

import io
import logging

import cv2
import numpy as np

log = logging.getLogger("partscan")

INK = "#1d2733"
DIM = "#c2410c"
HOLE = "#1d4ed8"


def _silhouette(V2, F, px_per_mm, pad_mm=2.0):
    lo = V2.min(0) - pad_mm
    hi = V2.max(0) + pad_mm
    W = int(np.ceil((hi[0] - lo[0]) * px_per_mm)) + 1
    H = int(np.ceil((hi[1] - lo[1]) * px_per_mm)) + 1
    img = np.zeros((H, W), np.uint8)
    pix = np.round((V2 - lo) * px_per_mm * 8).astype(np.int32)       # 3 bits of sub-pixel precision
    pix[:, 1] = H * 8 - 1 - pix[:, 1]                                  # v up
    from .geometry import fill_triangles
    fill_triangles(img, pix[F], 1, shift=3)                            # union of all faces
    return img, lo, H


def _contours(img, lo, H, px_per_mm):
    cnts, hier = cv2.findContours(img, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    out = []
    for c, h in zip(cnts, hier[0] if hier is not None else []):
        if len(c) < 6:
            continue
        p = c[:, 0, :].astype(float)
        mm = np.stack([p[:, 0] / px_per_mm + lo[0], (H - 1 - p[:, 1]) / px_per_mm + lo[1]], 1)
        out.append((mm, h[3] >= 0))   # (points, is_hole)
    return out


def _feature_edges(mesh, angle_deg=40.0, work_faces=20000):
    """Sharp edges (crease lines) for the drawing, found on a decimated + smoothed copy so that
    scan noise does not show up as speckle."""
    try:
        import trimesh
        m = mesh
        if len(m.faces) > work_faces * 1.5:
            m = m.simplify_quadric_decimation(face_count=work_faces)
        m = m.copy()
        trimesh.smoothing.filter_taubin(m, lamb=0.5, nu=0.53, iterations=10)
        ang = m.face_adjacency_angles
        e = m.face_adjacency_edges[ang > np.radians(angle_deg)]
        return m.vertices[e]
    except Exception:
        return np.zeros((0, 2, 3))


def _dim(ax, p0, p1, offset, text, horizontal=True):
    """Dimension line between p0 and p1 (2D), pushed out by offset (mm)."""
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    if horizontal:
        y = offset
        a, b = np.array([p0[0], y]), np.array([p1[0], y])
        ax.plot([p0[0], p0[0]], [p0[1], y], color=DIM, lw=0.6)
        ax.plot([p1[0], p1[0]], [p1[1], y], color=DIM, lw=0.6)
        ax.annotate("", a, b, arrowprops=dict(arrowstyle="<->", color=DIM, lw=0.9, shrinkA=0, shrinkB=0))
        ax.text((a[0] + b[0]) / 2, y, text, color=DIM, ha="center", va="bottom", fontsize=9, fontweight="bold",
                bbox=dict(fc="white", ec="none", pad=0.6))
    else:
        x = offset
        a, b = np.array([x, p0[1]]), np.array([x, p1[1]])
        ax.plot([p0[0], x], [p0[1], p0[1]], color=DIM, lw=0.6)
        ax.plot([p1[0], x], [p1[1], p1[1]], color=DIM, lw=0.6)
        ax.annotate("", a, b, arrowprops=dict(arrowstyle="<->", color=DIM, lw=0.9, shrinkA=0, shrinkB=0))
        ax.text(x, (a[1] + b[1]) / 2, text, color=DIM, ha="right", va="center", fontsize=9, fontweight="bold",
                rotation=90, bbox=dict(fc="white", ec="none", pad=0.6))


def _view(ax, mesh, ui, vi, title, circles, view_axis, px_per_mm, dims, mesh_small=None):
    V = mesh.vertices
    V2 = V[:, [ui, vi]]
    ms = mesh_small if mesh_small is not None else mesh
    img, lo, H = _silhouette(ms.vertices[:, [ui, vi]], ms.faces, px_per_mm)
    for pts, hole in _contours(img, lo, H, px_per_mm):
        ax.fill(pts[:, 0], pts[:, 1], color="white" if hole else "#e8edf3", zorder=1 if not hole else 2)
        ax.plot(np.r_[pts[:, 0], pts[0, 0]], np.r_[pts[:, 1], pts[0, 1]], color=INK, lw=1.1, zorder=3)
    E = _feature_edges(mesh)
    if len(E):
        seg = E[:, :, [ui, vi]]
        from matplotlib.collections import LineCollection
        ax.add_collection(LineCollection(seg, colors=INK, linewidths=0.35, alpha=0.55, zorder=3))
    umin, umax = V2[:, 0].min(), V2[:, 0].max()
    vmin, vmax = V2[:, 1].min(), V2[:, 1].max()
    span = max(umax - umin, vmax - vmin)
    off = 0.10 * span + 3
    _dim(ax, (umin, vmin), (umax, vmin), vmin - off, f"{dims[0]:.2f}", horizontal=True)
    _dim(ax, (umin, vmin), (umin, vmax), umin - off, f"{dims[1]:.2f}", horizontal=False)
    for c in circles:
        if c["axis"] != view_axis:
            continue
        cu, cv = c["center"][ui], c["center"][vi]
        r = c["diameter"] / 2
        t = np.linspace(0, 2 * np.pi, 120)
        ax.plot(cu + r * np.cos(t), cv + r * np.sin(t), color=HOLE, lw=1.0, zorder=4)
        ax.plot([cu - r * 1.25, cu + r * 1.25], [cv, cv], color=HOLE, lw=0.5, ls="-.", zorder=4)
        ax.plot([cu, cu], [cv - r * 1.25, cv + r * 1.25], color=HOLE, lw=0.5, ls="-.", zorder=4)
        lab = f"≈Ø{c['diameter']:.1f} (rough)" if c.get("rough") else f"Ø{c['diameter']:.2f}"
        ax.annotate(lab, (cu + r * 0.7, cv + r * 0.7), (cu + r + 0.06 * span, cv + r + 0.06 * span),
                    color=HOLE, fontsize=8.5, fontweight="bold",
                    arrowprops=dict(arrowstyle="-", color=HOLE, lw=0.7), zorder=5,
                    bbox=dict(fc="white", ec="none", pad=0.5))
    ax.set_title(title, fontsize=10.5, color=INK, loc="left", fontweight="bold")
    ax.set_aspect("equal")
    ax.set_xlim(umin - off - 0.12 * span - 4, umax + 0.22 * span + 4)
    ax.set_ylim(vmin - off - 0.08 * span - 4, vmax + 0.12 * span + 4)
    ax.axis("off")


def _iso(ax, mesh, faces_max=8000):
    m = mesh
    if len(m.faces) > faces_max:
        try:
            m = m.simplify_quadric_decimation(face_count=faces_max)
        except Exception:
            pass
    az, el = np.radians(-50), np.radians(28)
    Rz = np.array([[np.cos(az), -np.sin(az), 0], [np.sin(az), np.cos(az), 0], [0, 0, 1]])
    Rx = np.array([[1, 0, 0], [0, np.cos(el), -np.sin(el)], [0, np.sin(el), np.cos(el)]])
    R0 = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float)   # world Z -> screen up
    R = Rx @ R0 @ Rz
    V = m.vertices @ R.T
    T = V[m.faces]
    n = m.face_normals @ R.T
    light = np.array([0.3, 0.5, 0.8])
    light /= np.linalg.norm(light)
    vis = n[:, 2] > -0.05
    shade = 0.35 + 0.65 * np.clip(n @ light, 0, 1)
    if m.visual.kind == "vertex":
        col = m.visual.vertex_colors[m.faces][:, :, :3].mean(1) / 255.0
    else:
        col = np.tile([0.72, 0.75, 0.8], (len(m.faces), 1))
    col = np.clip(col * shade[:, None], 0, 1)
    order = np.argsort(T[:, :, 2].mean(1))
    order = order[vis[order]]
    from matplotlib.collections import PolyCollection
    pc = PolyCollection(T[order][:, :, :2], facecolors=col[order], edgecolors=col[order], linewidths=0.2)
    ax.add_collection(pc)
    ax.set_xlim(V[:, 0].min() - 3, V[:, 0].max() + 3)
    ax.set_ylim(V[:, 1].min() - 3, V[:, 1].max() + 3)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("Isometric", fontsize=10.5, color=INK, loc="left", fontweight="bold")


def ortho_drawing(mesh_mm, meas: dict, path: str, title: str = "") -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from .measure import best_dims
    o = meas["overall"]
    L, Wd, Hd, how = best_dims(meas)
    circles = meas.get("circles", [])
    ext = np.ptp(mesh_mm.vertices, 0)
    ppm = float(np.clip(900 / max(ext.max(), 1e-3), 4, 40))
    try:   # silhouettes are rasterised triangle by triangle: use a lighter copy of the mesh
        ms = mesh_mm.simplify_quadric_decimation(face_count=120000) if len(mesh_mm.faces) > 150000 else mesh_mm
    except Exception:
        ms = mesh_mm
    fig, axs = plt.subplots(2, 2, figsize=(11, 8.5), dpi=150)
    _view(axs[0, 0], mesh_mm, 0, 1, "Top  (X right, Y up)", circles, "Z", ppm, (L, Wd), ms)
    _iso(axs[0, 1], mesh_mm)
    _view(axs[1, 0], mesh_mm, 0, 2, "Front  (X right, Z up)", circles, "Y", ppm, (L, Hd), ms)
    _view(axs[1, 1], mesh_mm, 1, 2, "Side  (Y right, Z up)", circles, "X", ppm, (Wd, Hd), ms)
    fig.suptitle(title or f"PartScan  -  {L:.2f} x {Wd:.2f} x {Hd:.2f} mm ({how})   all dimensions in mm",
                 fontsize=12, color=INK, x=0.02, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path, facecolor="white")
    plt.close(fig)
    return path


def qa_montage(kf, masks, T_wc, mesh, path, n: int = 8) -> str:
    """Keyframes with the part silhouette (orange) and the reprojected final mesh (cyan)."""
    idx = np.linspace(0, len(kf) - 1, min(n, len(kf))).astype(int)
    try:
        ms = mesh.simplify_quadric_decimation(face_count=40000) if len(mesh.faces) > 60000 else mesh
    except Exception:
        ms = mesh
    tiles = []
    for i in idx:
        f = kf[i]
        rgb = f.rgb.copy()
        H, W = rgb.shape[:2]
        if masks[i] is not None:
            cnts, _ = cv2.findContours(masks[i].astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(rgb, cnts, -1, (255, 140, 0), max(2, W // 640))
        # reprojected mesh silhouette
        from .segment import mesh_silhouette
        sil = mesh_silhouette(ms.vertices, ms.faces, f.K, np.linalg.inv(T_wc[i]), (W, H)).astype(np.uint8)
        cnts, _ = cv2.findContours(sil, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(rgb, cnts, -1, (0, 200, 255), max(1, W // 960))
        ys, xs = np.nonzero(sil)
        if len(xs):
            cx, cy = int(xs.mean()), int(ys.mean())
            s = int(max(np.ptp(xs), np.ptp(ys)) * 0.9 + 60)
            x0, y0 = max(0, cx - s), max(0, cy - s)
            rgb = rgb[y0:min(H, cy + s), x0:min(W, cx + s)]
        tiles.append(cv2.resize(rgb, (360, 360), interpolation=cv2.INTER_AREA))
    while len(tiles) % 4:
        tiles.append(np.full((360, 360, 3), 255, np.uint8))
    rows = [np.concatenate(tiles[k:k + 4], 1) for k in range(0, len(tiles), 4)]
    img = np.concatenate(rows, 0)
    cv2.imwrite(path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])
    return path
