"""Self-contained HTML measurement report (images embedded, prints cleanly)."""
from __future__ import annotations

import base64
import datetime as _dt
import html
import os

import numpy as np


def _img(path):
    if not path or not os.path.exists(path):
        return ""
    ext = os.path.splitext(path)[1].lower().strip(".")
    mime = "jpeg" if ext in ("jpg", "jpeg") else ext
    return f"data:image/{mime};base64," + base64.b64encode(open(path, "rb").read()).decode()


def quality_rows(q: dict):
    """(label, value, status) with status in good / ok / poor / info."""
    def st(v, good, ok, higher_better=False):
        if v is None or (isinstance(v, float) and not np.isfinite(v)):
            return "info"
        if higher_better:
            return "good" if v >= good else ("ok" if v >= ok else "poor")
        return "good" if v <= good else ("ok" if v <= ok else "poor")
    rows = []
    ref = q.get("scale_reference", "")
    rows.append(("Scale reference", ref, "good" if "board" in ref.lower() else "ok"))
    if "board_frames" in q:
        rows.append(("Board seen in keyframes", f"{q['board_frames']} / {q.get('keyframes', '?')}",
                     st(q["board_frames"] / max(1, q.get("keyframes", 1)), 0.6, 0.3, True)))
    if "board_ba_rms_px" in q:
        rows.append(("Board reprojection error", f"{q['board_ba_rms_px']:.2f} px", st(q["board_ba_rms_px"], 0.5, 1.0)))
    if "arkit_scale_error_pct" in q:
        rows.append(("Phone tracking scale error (corrected)", f"{q['arkit_scale_error_pct']:+.2f} %", "info"))
    if "k1" in q:
        rows.append(("Lens distortion k1 (corrected)", f"{q['k1']:+.4f}", "info"))
    if "feature_points" in q:
        rows.append(("Photogrammetry tie points", f"{q['feature_points']:,}", st(q["feature_points"], 2000, 300, True)))
    if "ba_rms_tracks_px" in q:
        rows.append(("Tie-point reprojection error", f"{q['ba_rms_tracks_px']:.2f} px", st(q["ba_rms_tracks_px"], 0.8, 1.5)))
    if "silhouette_iou_median" in q:
        rows.append(("Model vs photo outlines (IoU)", f"{q['silhouette_iou_median']:.3f}",
                     st(q["silhouette_iou_median"], 0.95, 0.9, True)))
    if "outline_to_model_area_ratio" in q:
        rv = q["outline_to_model_area_ratio"]
        rows.append(("Photo outline area / model area", f"{rv:.3f}", st(abs(rv - 1), 0.02, 0.05)))
    if "depth_fit_sigma_mm" in q:
        rows.append(("Depth vs photogrammetry (per frame)", f"{q['depth_fit_sigma_mm']:.2f} mm", st(q["depth_fit_sigma_mm"], 1.0, 2.5)))
    if "depth_to_surface_rms_mm" in q:
        rows.append(("Depth vs final surface (RMS)", f"{q['depth_to_surface_rms_mm']:.2f} mm",
                     st(q["depth_to_surface_rms_mm"], 0.5, 1.0)))
    if "fusion_voxel_mm" in q:
        rows.append(("Voxel size", f"{q['fusion_voxel_mm']:.2f} mm", "info"))
    rows.append(("Depth model / segmentation", f"{q.get('depth_model', '?')} / {q.get('segmenter', '?')}", "info"))
    rows.append(("Frames used", f"{q.get('keyframes', '?')} of {q.get('frames_total', '?')}", "info"))
    return rows


CSS = """
:root{--ink:#16202b;--muted:#5b6876;--line:#dde3ea;--bg:#ffffff;--card:#f6f8fa;--good:#15803d;--ok:#b45309;--poor:#b91c1c;--accent:#c2410c}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--ink:#e8edf2;--muted:#9aa7b4;--line:#2c3540;--bg:#11161c;--card:#19202a;--good:#4ade80;--ok:#fbbf24;--poor:#f87171;--accent:#fb923c}}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1060px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:24px;margin:0 0 4px} h2{font-size:17px;margin:28px 0 10px}
.sub{color:var(--muted)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:18px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.kpi b{display:block;font-size:22px;font-variant-numeric:tabular-nums} .kpi span{color:var(--muted);font-size:13px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
td,th{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top} th{font-size:13px;color:var(--muted);font-weight:600}
td.v{white-space:nowrap;font-weight:600} td.n{color:var(--muted);font-size:13px}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:7px}
.good{background:var(--good)} .ok{background:var(--ok)} .poor{background:var(--poor)} .info{background:var(--line)}
img{max-width:100%;border:1px solid var(--line);border-radius:10px;background:#fff}
.warn{border-left:4px solid var(--ok);background:var(--card);padding:8px 12px;margin:6px 0;border-radius:6px}
.wrap{overflow-x:auto}
footer{color:var(--muted);font-size:12.5px;margin-top:30px}
@media print{.noprint{display:none}}
"""


def write_report(path, meas, q, warnings, cfg, rows, files) -> str:
    from .measure import best_dims
    o, m = meas["overall"], meas["mass"]
    holes = [c for c in meas.get("circles", []) if c["kind"] == "hole"]
    L, W, H, how = best_dims(meas)
    kpis = [(f"{L:.2f}", "Length X, mm"), (f"{W:.2f}", "Width Y, mm"), (f"{H:.2f}", f"Height Z, mm ({how})")]
    if m["volume_mm3"] == m["volume_mm3"]:
        kpis.append((f"{m['volume_mm3'] / 1000:.2f}", "Volume, cm³"))
    if m.get("mass_g") == m.get("mass_g") and m.get("mass_g"):
        kpis.append((f"{m['mass_g']:.0f}", f"Mass ({m['material']}), g"))
    n_rough = sum(1 for c in holes if c.get("rough"))
    kpis.append((str(len(holes)), "Holes found" + (f" ({n_rough} rough)" if n_rough else "")))
    e = html.escape
    kp = "".join(f'<div class="kpi"><b>{a}</b><span>{e(b)}</span></div>' for a, b in kpis)
    mt = "".join(f"<tr><td>{e(a)}</td><td class='v'>{e(b)}</td><td class='n'>{e(c)}</td></tr>" for a, b, c in rows)
    qt = "".join(f"<tr><td><span class='dot {s}'></span>{e(a)}</td><td class='v'>{e(str(b))}</td></tr>"
                 for a, b, s in quality_rows(q))
    wr = "".join(f"<div class='warn'>{e(w)}</div>" for w in warnings)
    draw = _img(files.get("drawing"))
    qa = _img(files.get("qa"))
    ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PartScan report</title><style>{CSS}</style></head><body><main>
<h1>Part measurement report</h1>
<div class="sub">{e(q.get('source', ''))} · {q.get('keyframes', '?')} keyframes · {e(q.get('scale_reference', ''))} · {ts}</div>
<div class="kpis">{kp}</div>
{wr}
<h2>Dimensioned views</h2>
{'<img src="' + draw + '" alt="Dimensioned drawing">' if draw else '<p class="sub">drawing unavailable</p>'}
<h2>Measurements</h2>
<p class="sub">Frame: the part as it rested on the board/table. Z is up from the support plane, X runs along the
longest side of the footprint, and the origin is the centre of the footprint. Values in mm unless noted.</p>
<div class="wrap"><table><tr><th>Feature</th><th>Value</th><th>Note</th></tr>{mt}</table></div>
<h2>Capture quality</h2>
<div class="wrap"><table>{qt}</table></div>
<p class="sub">Green/amber/red are rules of thumb. The only proof of accuracy for a given part is a caliper check:
measure one feature by hand, enter it as the reference and re-run - the report then shows the error.</p>
<h2>Silhouette check</h2>
<p class="sub">Orange = part outline found in the photo; cyan = outline of the final 3D model projected back. They should coincide.</p>
{'<img src="' + qa + '" alt="Silhouette QA">' if qa else ''}
<footer>Generated by PartScan (Colab). Meshes: part_mm.stl / .obj / .ply in millimetres, part.glb in metres.</footer>
</main></body></html>"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return path
