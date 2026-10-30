"""Gradio interface (works inline in Colab and on a phone browser)."""
from __future__ import annotations

import datetime as _dt
import glob
import html
import logging
import os
import traceback

from .config import BOARDS, MATERIAL_DENSITY_G_CM3, PRESETS, SCALE_CHECKS, BoardSpec, board_from_check, make_config

log = logging.getLogger("partscan")

GUIDE = """
### What you need
* **iPhone 17 Pro / Pro Max** (or any iPhone / iPad **Pro** with LiDAR). The regular iPhone 17 and the iPhone Air have no LiDAR.
* The free app **Stray Scanner** (App Store). Record3D `.r3d`, Polycam *raw data* and 3D Scanner App *All Data* exports also work.
* The **PartScan board** (tab *2 · Board*): print at **100 %**, glue it flat to something rigid, then
  measure the printed **100 mm ruler** with a ruler or caliper and type what you measured into the *Print check* box.
  That single number sets the scale of everything.

### Prepare the part
* Place the part **near the middle of the board**, resting on its most stable face. Nothing else on the board.
* Shiny, black or transparent parts confuse every optical sensor: dust them lightly with **3D-scanning spray** or
  developer spray (or dry shampoo). Spray outdoors or with good ventilation, away from flames; wear a mask with powders.
  Coatings add roughly 10-50 µm - clean them off precision surfaces afterwards.
* Even, diffuse light (no hard shadows, no direct sun, no glare on the board). Don't move the part or the board while scanning.

### Record (40-90 s, slowly)
1. Keep the phone **30-40 cm** from the part (the LiDAR does not work much closer than ~25 cm).
2. **Low ring**: go all the way around at about table height + 10 cm, camera aimed at the part.
3. **Middle ring** all the way around at ~45°.
4. **High ring** at ~70°, then a slow **pass right over the top**, looking straight down (needed to measure holes).
5. Holes in the *sides*? Finish with a few slow views looking straight into each one, the phone level with the
   hole (rest it on the table if needed) - otherwise those holes come out too small and are marked *rough*.
6. Move slowly and smoothly - sharp frames matter more than many frames. Keep the part roughly centred and the board in view.

### Get the scan into this notebook
Stray Scanner → **Library** → your scan → **Share** → *Save to Files* → **Google Drive › PartScan**
(a zip or a folder both work). Then press **↻**, pick it under *…or a scan from Google Drive*, and **Build 3D model**.
On the free Colab tier the phone is for capturing; run the app here in the notebook (on a computer or the phone's browser).

### Reading the results
* **Length × width × height** are measured with the part as it rested on the board: Z is up from the board,
  X runs along the longest side of the footprint. *Between faces* values come from fitted planar faces (most precise);
  the *envelope* is the outer extent including scan noise.
* *Wall thickness / width* and *step* rows are distances between parallel faces.
* **Holes** are measured on cross-sections; the Ø is the median over the middle of the bore. A hole marked
  **rough** was only partly seen (typically a side hole filmed at an angle): its Ø is uncertain and usually too small.
* Always check one number with a caliper (*Caliper check*): the report then shows the real error of this scan.
"""

CSS = """
#ps-title h1{margin-bottom:0}
.ps-small{font-size:.9em;opacity:.8}
footer{display:none !important}
"""


def _is_scan_dir(d):
    try:
        names = set(os.listdir(d))
    except OSError:
        return False
    return ({"odometry.csv", "rgb.mp4"} <= names or ("metadata" in names and "rgbd" in names)
            or "keyframes" in names or any(n.startswith("frame_") and n.endswith(".json") for n in names))


def _list_drive(folder):
    """(label, path) for every scan in the Drive folder: zips, .r3d files and exported folders."""
    if not folder or not os.path.isdir(folder):
        return []
    items = []
    for root, dirs, files in os.walk(folder):
        if os.path.relpath(root, folder).count(os.sep) > 2 or "results" in os.path.relpath(root, folder).split(os.sep):
            dirs[:] = []
            continue
        for fn in files:
            if fn.lower().endswith((".zip", ".r3d")):
                items.append(os.path.join(root, fn))
        for dn in list(dirs):
            if _is_scan_dir(os.path.join(root, dn)):
                items.append(os.path.join(root, dn))
                dirs.remove(dn)
    items.sort(key=os.path.getmtime, reverse=True)
    out = []
    for p in items:
        when = _dt.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%b %d %H:%M")
        out.append((f"{os.path.relpath(p, folder)}  ·  {when}", p))
    return out


BOARD_PAPERS = (("Letter", "Standard 8x6 (Letter / A4)"), ("A4", "Standard 8x6 (Letter / A4)"),
                ("A3", "Large 11x7 (A3 / Tabloid)"), ("Tabloid", "Large 11x7 (A3 / Tabloid)"))
BOARD_SETS = (("Boards", True), ("Boards_no_text", False))     # sub-folder, captions on the print?


def export_boards(base_folder: str) -> list:
    """Write the printable boards under `base_folder`: Boards/ (with captions) and Boards_no_text/ (pattern and
    100 mm ruler only), one PDF per paper size. Files that already exist are kept. Returns the paths."""
    from .board import render_board_pdf
    out = []
    for sub, text in BOARD_SETS:
        folder = os.path.join(base_folder, sub)
        os.makedirs(folder, exist_ok=True)
        for paper, board_name in BOARD_PAPERS:
            path = os.path.join(folder, f"PartScan_board_{paper}{'' if text else '_no_text'}.pdf")
            if not os.path.exists(path):
                try:
                    render_board_pdf(BoardSpec(**BOARDS[board_name]), path, paper=paper, text=text)
                except Exception as ex:
                    log.warning("could not write %s: %s", path, ex)
                    continue
            out.append(path)
    return out


def boards_folder_for(drive_dir: str) -> str:
    """Where the board folders go on Google Drive: MyDrive/ShapeMeasure if that project folder exists,
    otherwise next to the scans in MyDrive/PartScan."""
    my_drive = os.path.dirname(os.path.abspath(drive_dir.rstrip("/")))
    shape = os.path.join(my_drive, "ShapeMeasure")
    return shape if os.path.isdir(shape) else drive_dir


def _viewer_iframe(path, height=560):
    if not path:
        return ""
    src = "/gradio_api/file=" + html.escape(os.path.abspath(path))
    return (f'<iframe src="{src}" style="width:100%;height:{height}px;border:1px solid rgba(127,127,127,.25);'
            f'border-radius:12px" allow="fullscreen"></iframe>'
            f'<div class="ps-small">Tap two points to measure. <a href="{src}" target="_blank">Open full screen</a></div>')


def _summary(res):
    from .export import measurement_rows  # noqa
    m = res.measurements
    o = m["overall"]
    q = res.quality
    holes = [c for c in m.get("circles", []) if c["kind"] == "hole"]
    from .measure import best_dims
    L, W, H, how = best_dims(m)
    lines = [f"### {L:.2f} × {W:.2f} × {H:.2f} mm  <span style='font-size:.6em;opacity:.7'>({how})</span>",
             f"Scale from **{q.get('scale_reference', '?')}** · {q.get('keyframes', '?')} keyframes · "
             f"{len(holes)} hole(s)" + (f" ({sum(1 for c in holes if c.get('rough'))} rough)"
                                        if any(c.get("rough") for c in holes) else "")
             + f" · {res.seconds / 60:.1f} min"]
    ref = m.get("reference")
    if ref and ref.get("measured_mm") is not None:
        lines.append(f"Caliper check ({ref['name']}): scan {ref['measured_mm']:.3f} mm vs yours "
                     f"{ref['reference_mm']:.3f} mm → **{ref['error_mm']:+.3f} mm** ({ref['error_pct']:+.2f} %)"
                     + (" - model rescaled to match." if ref.get("applied") else ""))
    for w in res.warnings:
        lines.append(f"> ⚠️ {w}")
    return "\n\n".join(lines)


def build_app(out_root: str = "/content/partscan_runs", drive_dir: str | None = None):
    import gradio as gr

    from . import pipeline
    from .board import render_board_pdf
    from .export import measurement_rows
    from .report import quality_rows

    os.makedirs(out_root, exist_ok=True)

    def run(upload, drive_file, preset, use_board, board_type, check_mm, check_on, material, ref_name, ref_val,
            ref_apply, progress=gr.Progress()):
        path = upload if upload else drive_file
        if not path:
            raise gr.Error("Upload a scan (.zip / .r3d) or pick one from Google Drive first.")
        try:
            board, note = board_from_check(board_type, float(check_mm or 0), check_on)
            cfg = make_config(preset, use_board=bool(use_board), board=board, material=material,
                              reference_name=ref_name or "", reference_mm=float(ref_val or 0),
                              apply_reference_scale=bool(ref_apply))
            stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            run_dir = os.path.join(out_root, stamp)
            progress(0, desc="starting (the first run downloads the AI models, about 2 GB)")
            if not pipeline.RUN_LOCK.acquire(timeout=1.0):
                progress(0, desc="waiting for another scan to finish")
                pipeline.RUN_LOCK.acquire()
            try:
                res = pipeline.run(path, run_dir, cfg, progress=lambda p, m: progress(p, desc=m))
            finally:
                pipeline.RUN_LOCK.release()
            if note and use_board:
                res.warnings.insert(0, note)
            if drive_dir and res.files.get("zip"):
                try:   # keep a copy of the results next to the scans
                    import shutil
                    os.makedirs(os.path.join(drive_dir, "results"), exist_ok=True)
                    base = os.path.splitext(os.path.basename(str(path).rstrip("/")))[0]
                    shutil.copy(res.files["zip"], os.path.join(drive_dir, "results", f"{base}_{stamp}.zip"))
                except Exception as ex:
                    log.warning("could not copy results to Drive: %s", ex)
        except Exception as ex:
            log.error(traceback.format_exc())
            raise gr.Error(f"{type(ex).__name__}: {ex}", duration=None)
        f = res.files
        downloads = [f[k] for k in ("zip", "stl", "obj", "ply", "glb", "report", "viewer", "csv", "json")
                     if f.get(k) and os.path.exists(f[k])]
        rows = [list(r) for r in measurement_rows(res.measurements)]
        qrows = [[("🟢" if s == "good" else "🟠" if s == "ok" else "🔴" if s == "poor" else "⚪") + " " + a, str(b)]
                 for a, b, s in quality_rows(res.quality)]
        return (_summary(res), _viewer_iframe(f.get("viewer")), rows, f.get("drawing"), downloads, f.get("glb"),
                qrows, f.get("qa"))

    def refresh():
        return gr.update(choices=_list_drive(drive_dir), value=None)

    def make_board(paper):
        name = "Large 11x7 (A3 / Tabloid)" if paper in ("A3", "Tabloid") else "Standard 8x6 (Letter / A4)"
        spec, _ = board_from_check(name, 100.0)
        p = os.path.join(out_root, f"PartScan_board_{paper}.pdf")
        render_board_pdf(spec, p, paper)
        return p

    if drive_dir:
        try:
            folder = boards_folder_for(drive_dir)
            if export_boards(folder):
                log.info("printable boards are in %s (Boards, Boards_no_text)", folder)
        except Exception as ex:
            log.warning("could not export the boards to Drive: %s", ex)

    with gr.Blocks(title="PartScan") as demo:
        gr.Markdown("# PartScan  ·  iPhone LiDAR → measured 3D model", elem_id="ps-title")
        gr.Markdown("Upload a scan of **one small part** on the printed board. You get a true-scale mesh "
                    "(STL/OBJ/PLY/GLB), dimensioned views, holes, wall thicknesses and an interactive measuring viewer.",
                    elem_classes="ps-small")
        with gr.Tab("1 · Scan"):
            with gr.Row(equal_height=False):
                with gr.Column(scale=1, min_width=300):
                    upload = gr.File(label="Scan (.zip from Stray Scanner, .r3d, Polycam / 3D Scanner App zip)",
                                     file_types=[".zip", ".r3d"], type="filepath")
                    with gr.Row():
                        drive = gr.Dropdown(_list_drive(drive_dir), label="…or a scan from Google Drive"
                                            + (f" ({drive_dir})" if drive_dir else ""), interactive=True,
                                            visible=drive_dir is not None, scale=4)
                        rf = gr.Button("↻", visible=drive_dir is not None, scale=1, min_width=50)
                    preset = gr.Radio(list(PRESETS), value="Accurate (recommended)", label="Quality")
                    with gr.Accordion("Calibration board", open=True):
                        use_board = gr.Checkbox(True, label="Part is on the printed PartScan board")
                        board_type = gr.Radio(list(BOARDS), value="Standard 8x6 (Letter / A4)", label="Board")
                        check_mm = gr.Number(100.0, label="Print check: what you measured (mm)",
                                             info="Printers scale by up to ~1 %. Measure the printed 100 mm ruler.")
                        check_on = gr.Dropdown(list(SCALE_CHECKS), value="printed 100 mm ruler",
                                               label="Measured on")
                    with gr.Accordion("Caliper check (optional, recommended)", open=False):
                        ref_name = gr.Dropdown(pipeline.REFERENCE_CHOICES, label="Feature you measured by hand")
                        ref_val = gr.Number(0, label="Your measurement (mm)")
                        ref_apply = gr.Checkbox(False, label="Rescale the model so this feature matches exactly",
                                                info="Only length/width/height, and only for corrections up to 3 %.")
                    material = gr.Dropdown(list(MATERIAL_DENSITY_G_CM3), value="Steel", label="Material (for mass)")
                    go = gr.Button("Build 3D model", variant="primary", size="lg")
                with gr.Column(scale=2, min_width=320):
                    status = gr.Markdown("Results appear here.")
                    viewer = gr.HTML()
                    table = gr.Dataframe(headers=["Feature", "Value (mm)", "Note"], label="Measurements",
                                         wrap=True, interactive=False, column_widths=["34%", "16%", "50%"])
                    drawing = gr.Image(label="Dimensioned views", type="filepath", interactive=False)
                    files = gr.File(label="Downloads (zip = everything)", file_count="multiple", interactive=False)
                    with gr.Accordion("Model preview (Gradio viewer) & capture quality", open=False):
                        m3d = gr.Model3D(label="Mesh", clear_color=(0.96, 0.97, 0.98, 1.0), height=420)
                        quality = gr.Dataframe(headers=["Check", "Value"], label="Capture quality", interactive=False)
                        qa = gr.Image(label="Silhouette check (orange = photo outline, cyan = 3D model)",
                                      type="filepath", interactive=False)
            go.click(run, [upload, drive, preset, use_board, board_type, check_mm, check_on, material, ref_name,
                           ref_val, ref_apply], [status, viewer, table, drawing, files, m3d, quality, qa])
            rf.click(refresh, None, drive)
        with gr.Tab("2 · Board"):
            gr.Markdown("Print **at 100 % / Actual size** (not *Fit to page*) on matte paper, glue it flat to card, "
                        "glass or a clipboard, then measure the printed 100 mm ruler and enter the value on the first tab. "
                        "Letter / A4 give the standard 8×6 board (parts up to ~120 mm); A3 / Tabloid give the "
                        "large 11×7 board (parts up to ~200 mm) - select the matching board on the first tab.")
            paper = gr.Radio(["Letter", "A4", "A3", "Tabloid"], value="Letter", label="Paper")
            pb = gr.Button("Create board PDF")
            pdf = gr.File(label="Board PDF", interactive=False)
            pb.click(make_board, [paper], pdf)
        with gr.Tab("3 · Guide"):
            gr.Markdown(GUIDE)
    return demo


def launch(demo, out_root: str, share: bool = False, debug: bool = False, height: int = 1100, **kw):
    """Start the app. pwa=True makes it installable on a phone (Safari: Share -> Add to Home Screen)."""
    import gradio as gr
    theme = gr.themes.Soft(primary_hue="orange", neutral_hue="slate")
    return demo.launch(share=share, debug=debug, allowed_paths=[out_root], theme=theme, css=CSS,
                       height=height, show_error=True, pwa=True, **kw)


PHONE_STEPS = """**PartScan iPhone app (TestFlight):** open the *Server* tab, tap **Scan QR code** and point it at the code
below - that connects the app to this notebook. Then *Scan → Record → Upload & build*.

**Without the app (Safari):** scan the code with the Camera app, open the link, then *Share → Add to Home Screen*.
Record with **Stray Scanner** (the scan is a folder under *Files → On My iPhone → Stray Scanner*): move it to
*Google Drive › PartScan* and press ↻ in PartScan, or long-press it → **Compress** and upload the .zip (use Wi-Fi).

Either way keep this notebook tab open on the computer; a scan takes ~10 min on a T4.
"""


def phone_link(demo, out_dir: str, api_key: str | None = None):
    """QR code + instructions for using the running app from a phone. Returns (png_path or None, url or None).
    With an access key the QR carries it (?k=...): the PartScan iPhone app reads both from one scan."""
    url = getattr(demo, "share_url", None) or getattr(demo, "local_url", None)
    if not url:
        return None, None
    if api_key:
        url = url.rstrip("/") + "/?k=" + api_key
    png = None
    try:
        import qrcode
        os.makedirs(out_dir, exist_ok=True)
        png = os.path.join(out_dir, "phone_link_qr.png")
        qrcode.make(url).save(png)
    except Exception as ex:                               # qrcode not installed: the link alone still works
        log.info("no QR code (%s)", ex)
    return png, url
