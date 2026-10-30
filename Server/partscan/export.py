"""Write every deliverable: meshes, drawings, reports, interactive viewer, zip."""
from __future__ import annotations

import csv
import json
import logging
import os
import zipfile

import numpy as np

log = logging.getLogger("partscan")


def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return jsonable(o.tolist())
    if isinstance(o, (np.floating,)):
        v = float(o)
        return None if not np.isfinite(v) else v
    if isinstance(o, float):
        return None if not np.isfinite(o) else o
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def measurement_rows(meas: dict) -> list:
    """Flat (feature, value_mm, note) rows used by the CSV, the UI table and the report."""
    o, m = meas["overall"], meas["mass"]
    bf = o.get("between_faces", {})
    rows = []
    for ax, key, what in (("X", "length_x", "Length X"), ("Y", "width_y", "Width Y"), ("Z", "height_z", "Height Z")):
        if ax in bf:
            rows.append((f"{what} (between faces)", bf[ax], f"plane fit; envelope incl. scan noise {o[key]:.2f}"))
        else:
            rows.append((f"{what} (envelope)", o[key], "max extent" + (" above the support plane" if ax == "Z" else
                                                                         ", footprint rectangle")))
    rows.append(("Oriented box (min volume)", " x ".join(f"{v:.2f}" for v in o["obb_extents"]), "any orientation"))
    if m["volume_mm3"] == m["volume_mm3"]:
        rows.append(("Volume", f"{m['volume_mm3'] / 1000:.3f} cm³", "" if m["watertight"] else "from the fused volume"))
    rows.append(("Surface area", f"{m['area_mm2'] / 100:.2f} cm²", ""))
    if m.get("mass_g") == m.get("mass_g") and m.get("mass_g"):
        rows.append((f"Mass ({m['material']})", f"{m['mass_g']:.1f} g", f"{m['density_g_cm3']} g/cm³"))
    for k, c in enumerate(meas.get("circles", []), 1):
        what = "Hole" if c["kind"] == "hole" else "Round feature"
        ctr = ", ".join(f"{v:.2f}" for v in c["center"])
        if c.get("rough"):
            rows.append((f"{what} {k} Ø (axis {c['axis']}) - ROUGH, Ø uncertain", c["diameter"],
                         f"bore only partly captured, probably undersized: scan straight into it or measure it "
                         f"directly; centre ({ctr}), length {c['length']:.1f}"))
            continue
        rows.append((f"{what} {k} Ø (axis {c['axis']})", c["diameter"],
                     f"centre ({ctr}), length {c['length']:.1f}, spread {c['diameter_spread']:.2f}"))
    for r in meas.get("plane_relations", []):
        name = "Wall thickness / width" if r["kind"] == "thickness" else "Step height"
        from .measure import axis_label
        rows.append((f"{name} (along {axis_label(r['normal'])})", r["distance"], "between parallel faces"))
    ref = meas.get("reference")
    if ref and ref.get("measured_mm") is not None:
        rows.append((f"Caliper check: {ref['name']}", ref["measured_mm"],
                     f"you measured {ref['reference_mm']:.3f} -> error {ref['error_mm']:+.3f} mm "
                     f"({ref['error_pct']:+.2f}%)" + (" - scale corrected" if ref.get("applied") else "")))
    out = []
    for name, val, note in rows:
        out.append((name, f"{val:.3f}" if isinstance(val, (float, np.floating)) else str(val), note))
    return out


def export_all(out_dir, mesh_m, mesh_mm, meas, quality, warnings, cfg, qa=None, progress=None,
               extra_files=None) -> dict:
    from . import drawings, report, viewer
    files = dict(extra_files or {})
    p = lambda k, frac: progress(frac, k) if progress else None
    p("meshes", 0.05)
    files["stl"] = os.path.join(out_dir, "part_mm.stl")
    mesh_mm.export(files["stl"])
    files["obj"] = os.path.join(out_dir, "part_mm.obj")
    mesh_mm.export(files["obj"])
    files["ply"] = os.path.join(out_dir, "part_mm.ply")
    mesh_mm.export(files["ply"])
    files["glb"] = os.path.join(out_dir, "part.glb")
    mesh_m.export(files["glb"])
    p("drawings", 0.3)
    try:
        files["drawing"] = drawings.ortho_drawing(mesh_mm, meas, os.path.join(out_dir, "drawing.png"))
    except Exception as ex:
        log.warning("drawing failed: %s", ex)
    if qa is not None:
        try:
            files["qa"] = drawings.qa_montage(qa["kf"], qa["masks"], qa["T_wc"], mesh_m,
                                              os.path.join(out_dir, "qa_silhouettes.jpg"))
        except Exception as ex:
            log.warning("QA montage failed: %s", ex)
    p("tables", 0.6)
    rows = measurement_rows(meas)
    files["csv"] = os.path.join(out_dir, "measurements.csv")
    with open(files["csv"], "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["feature", "value (mm unless noted)", "note"])
        w.writerows(rows)
    p("viewer", 0.7)
    try:
        files["viewer"] = viewer.write_viewer(os.path.join(out_dir, "viewer.html"), mesh_m, meas)
    except Exception as ex:
        log.warning("viewer failed: %s", ex)
    p("report", 0.85)
    files["report"] = report.write_report(os.path.join(out_dir, "report.html"), meas, quality, warnings, cfg,
                                          rows, files)
    files["json"] = os.path.join(out_dir, "report.json")
    with open(files["json"], "w") as fh:
        json.dump(jsonable(dict(measurements=meas, quality=quality, warnings=warnings, config=cfg.to_dict())), fh, indent=1)
    files["zip"] = os.path.join(out_dir, "partscan_results.zip")
    with zipfile.ZipFile(files["zip"], "w", zipfile.ZIP_DEFLATED) as z:
        for k, f in files.items():
            if k == "zip" or not f or not os.path.exists(f):
                continue
            z.write(f, os.path.basename(f))
    return files
