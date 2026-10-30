"""Assemble PartScan_LiDAR.ipynb from the partscan/ package."""
import json
import os
import sys

import nbformat as nbf

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "Server", "partscan")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "PartScan_LiDAR.ipynb")

MODULES = ["__init__", "config", "geometry", "io_scans", "board", "bundle", "poses", "segment", "depth",
           "fusion", "meshproc", "measure", "drawings", "viewer", "report", "export", "pipeline", "app", "api"]

INTRO = """# 📐 PartScan — iPhone LiDAR → accurate, measured 3D model of a part

Scan one small part (a bracket, housing, fixture, casting…) with an **iPhone 17 Pro / Pro Max** and get:

* a **true-scale 3D mesh** — `STL` / `OBJ` / `PLY` in millimetres plus `GLB`
* **dimensioned views** (top · front · side · isometric)
* automatic **measurements** — length × width × height, oriented bounding box, volume, area, mass,
  **hole diameters and positions**, **wall thicknesses**, **step heights**
* an **interactive 3D viewer** (phone friendly) — tap two points to measure anything
* an HTML **report** with capture-quality checks and an optional caliper cross-check

### How the accuracy is achieved
| Step | What | Why |
|---|---|---|
| Scale & poses | A printed **ChArUco board** under the part + bundle adjustment (with lens-distortion estimation) | Phone tracking alone is typically 0.5–2 % off in scale; the board fixes scale and gives drift-free camera poses |
| Photogrammetry | SIFT tie points triangulated across views | Independent, multi-view geometry to correct every depth map |
| AI depth | **Prompt Depth Anything** (CVPR 2025) — RGB + the phone's 256×192 LiDAR map → sharp metric depth | Raw iPhone LiDAR is ~±1 cm and blind to small details |
| AI silhouettes | **SAM 2.1**, prompted automatically from 3D | Pixel-sharp outlines carve away the edge smear that depth sensors produce |
| Fusion | GPU TSDF ∩ robust visual hull, closed on the support plane | Watertight, true-scale solid |
| Metrology | Plane fits, cross-section circle fits, min-area footprint, oriented box | Numbers you can compare with a caliper |

### You need
* **iPhone 17 Pro or 17 Pro Max** (iPhone 12 Pro – 16 Pro Max and LiDAR iPad Pros work too). ⚠️ The plain
  **iPhone 17 and iPhone Air have no LiDAR.**
* The free app **Stray Scanner** (App Store). Record3D `.r3d`, Polycam *raw data* and 3D Scanner App *All Data* zips also work.
* The **PartScan board** — make the PDF in the app's tab *2 · Board* (or use the board PDFs that came with this
  notebook), print it at 100 % and measure its printed 100 mm ruler; you enter that value in the app.
* A **GPU runtime**: *Runtime → Change runtime type → T4 GPU* (free tier is fine).

### Quick start
1. **Runtime → Run all.** The app appears in section 3 (the first run downloads ≈2 GB of model weights).
2. Tab **2 · Board** → print at 100 % → measure the printed 100 mm ruler.
3. Tab **3 · Guide** → record with Stray Scanner → *Share → Save to Files → Google Drive › PartScan* →
   press ↻ in tab 1, pick the scan → **Build 3D model** (roughly 10 min on a T4; *Fast* about half).

**Using it on the iPhone:** set `PUBLIC_LINK = True` in section 3 — the cell then shows a QR code; scan it, and in
Safari choose *Share → Add to Home Screen* to install PartScan as an app on the phone (the notebook keeps running
the heavy lifting on the GPU). This needs Colab Pro / a compute-unit balance: Google's FAQ disallows using a
**free** runtime mainly through a web UI. For a permanent address, see *Hosting it yourself* at the end.
"""

SETUP = """#@title 1 · Setup (≈1 min) { display-mode: "form" }
import os, sys, subprocess, importlib
try:
    import torch
    gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
except Exception:
    gpu = None
print("GPU:", gpu or "NONE - choose Runtime > Change runtime type > T4 GPU (CPU works but is very slow)")
pkgs = ["trimesh", "av", "fast-simplification", "qrcode"]
missing = [p for p in pkgs if importlib.util.find_spec(p.replace("-", "_")) is None]
if missing:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", *missing], check=True)
import transformers, gradio, cv2, numpy
print("transformers", transformers.__version__, "| gradio", gradio.__version__, "| opencv", cv2.__version__,
      "| numpy", numpy.__version__)
os.makedirs("/content/partscan", exist_ok=True)
os.chdir("/content")
print("ready")"""

ENGINE_MD = """## 2 · Engine code
Each cell below writes one stage of the pipeline into the `partscan/` package. **Just run them** — or open
any stage to read or tweak it (re-run the cell, then re-run section 4).

| module | role |
|---|---|
| `io_scans` | readers for Stray Scanner / Record3D / Polycam / 3D Scanner App, sharp-frame & keyframe selection |
| `board` | ChArUco board generation (print-ready PDF), detection, robust PnP |
| `bundle`, `poses` | SIFT tracks, Levenberg-Marquardt bundle adjustment (Schur complement), lens distortion |
| `segment` | locating the part from LiDAR, SAM 2.1 silhouettes with guided-filter edge refinement |
| `depth` | Prompt Depth Anything (LiDAR-guided AI depth) + per-frame metric correction |
| `fusion` | GPU TSDF ∩ robust visual hull ∩ support plane, marching cubes |
| `meshproc`, `measure` | clean-up, canonical part frame, colour, planes / holes / dimensions |
| `drawings`, `viewer`, `report`, `export` | dimensioned views, interactive 3D viewer, HTML report, files |
| `pipeline`, `app`, `api` | orchestration, the Gradio interface and the REST API used by the iPhone app |
"""

DRIVE_MD = """## 3 · Start PartScan
* **Google Drive** (recommended for phone scans): the cell mounts your Drive and creates `MyDrive/PartScan`.
  On the iPhone: Stray Scanner → Library → scan → **Share → Save to Files → Google Drive › PartScan**.
  Then press **↻** in the app and pick the scan.
* The app runs **inside this notebook**. `PUBLIC_LINK = True` additionally creates a temporary public link
  (`gradio.live`, lasts up to a week) and shows a **QR code** to open the app on the iPhone and add it to the home
  screen. Google's Colab FAQ disallows *"bypassing the notebook UI to interact primarily via a web UI"* on **free**
  runtimes (allowed with a positive compute-unit balance), so it is off by default. The runtime must stay alive:
  keep this notebook tab open on a computer (Colab disconnects idle free sessions after a while).
"""

LAUNCH = """#@title 3 · Start the app { display-mode: "form" }
USE_GOOGLE_DRIVE = True   #@param {type:"boolean"}
PUBLIC_LINK = False       #@param {type:"boolean"}
PRELOAD_AI_MODELS = True  #@param {type:"boolean"}
ACCESS_KEY = ""           #@param {type:"string"}
import logging, os, sys
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S", force=True)
for k in [k for k in sys.modules if k == "partscan" or k.startswith("partscan.")]:
    del sys.modules[k]                                   # pick up edits to the engine cells
OUT = "/content/partscan_runs"
drive_dir = None
if USE_GOOGLE_DRIVE:
    try:
        from google.colab import drive
        drive.mount("/content/drive")
        drive_dir = "/content/drive/MyDrive/PartScan"
        os.makedirs(drive_dir, exist_ok=True)
        print("Put scans in Google Drive > PartScan")
    except Exception as ex:
        print("Google Drive not mounted:", ex)
from partscan import pipeline
from partscan.config import make_config
if PRELOAD_AI_MODELS:
    dev = pipeline.resolve_device("auto")
    cfg0 = make_config()
    print("loading Prompt Depth Anything + SAM 2.1 on", dev, "(first time ≈1-2 min)...")
    pipeline.get_depth_model(cfg0, dev)
    pipeline.get_segmenter(cfg0, dev)
    print("models ready")
from partscan.app import build_app, launch, phone_link, PHONE_STEPS
from partscan.api import attach_api, new_key
demo = build_app(OUT, drive_dir)
app, local_url, share_url = launch(demo, OUT, share=PUBLIC_LINK)
key = ACCESS_KEY.strip() or new_key()          # protects the phone-app API; blank = a new random key each start
attach_api(app, OUT, key)
if PUBLIC_LINK:
    from IPython.display import Image, Markdown, display
    png, url = phone_link(demo, OUT, key)
    if url:
        display(Markdown(f"### Phone link: `{url}`  \\n(server `{share_url}`, access key `{key}`)  \\n" + PHONE_STEPS))
        if png:
            display(Image(png, width=220))
    else:
        print("No public link was created (see the messages above).")"""

MANUAL_MD = """## 4 · (Optional) Run without the app
Process a scan from a path and show the results right here. Handy for batches or for scripting."""

MANUAL = """#@title 4 · Run on a file { display-mode: "form" }
SCAN_PATH = "/content/drive/MyDrive/PartScan/my_scan.zip"  #@param {type:"string"}
QUALITY = "Accurate (recommended)"  #@param ["Fast (fewer frames)", "Accurate (recommended)", "Max detail (slow)"]
BOARD = "Standard 8x6 (Letter / A4)"  #@param ["Standard 8x6 (Letter / A4)", "Large 11x7 (A3 / Tabloid)"]
SCALE_CHECK_MM = 100.0   #@param {type:"number"}
SCALE_CHECK_OF = "printed 100 mm ruler"  #@param ["printed 100 mm ruler", "6 squares between inner corners"]
ON_BOARD = True          #@param {type:"boolean"}
MATERIAL = "Steel"       #@param ["Steel", "Stainless steel", "Aluminium 6061", "Titanium Ti-6Al-4V", "Brass", "Copper", "Cast iron", "PLA", "ABS", "Nylon (PA12)", "POM / Delrin", "None"]
import os, datetime, pandas as pd
from IPython.display import display, Image, Markdown
from partscan import pipeline
from partscan.config import make_config, board_from_check
from partscan.export import measurement_rows
from partscan.measure import best_dims

def run_file():
    board, note = board_from_check(BOARD, SCALE_CHECK_MM, SCALE_CHECK_OF)
    if note:
        print("NOTE:", note)
    cfg = make_config(QUALITY, use_board=ON_BOARD, board=board, material=MATERIAL)
    run_dir = os.path.join("/content/partscan_runs", datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    last = [-1]
    def prog(p, m):
        if int(p * 20) != last[0]:
            last[0] = int(p * 20); print(f"{p:5.0%}  {m}")
    res = pipeline.run(SCAN_PATH, run_dir, cfg, progress=prog)
    L, W, H, how = best_dims(res.measurements)
    display(Markdown(f"### {L:.2f} × {W:.2f} × {H:.2f} mm ({how})"))
    for w in res.warnings:
        print("WARNING:", w)
    display(pd.DataFrame(measurement_rows(res.measurements), columns=["feature", "value (mm)", "note"]))
    display(Image(res.files["drawing"]))
    print("All files:", run_dir)
    try:
        from google.colab import files
        files.download(res.files["zip"])
    except Exception:
        pass
    return res

if os.path.exists(SCAN_PATH):
    res = run_file()
else:
    print(f"Set SCAN_PATH to your scan first (not found: {SCAN_PATH}). The app above does the same job.")"""

TIPS = """## Troubleshooting
**"No calibration board detected"** — keep the whole board in view in most frames and avoid glare (matte paper, soft
light). Without the board, scale comes from the phone's motion tracking (typically 0.5–2 % off): enter a caliper
reference and tick *Rescale*.

**Holes come out too small or closed** — the camera has to look *into* a hole to see its walls: add the slow pass
straight above the part, and look straight into holes on the sides. Holes under ~4 mm are at the limit of a phone.

**Bumpy, missing or cut-down surfaces** — shiny, black or transparent surfaces: use scanning spray. Move slowly (motion
blur), add light. Check the *Silhouette check* image and the *Depth vs final surface* row in the report.

**Very thin walls and fins (under ~1.5 mm on a part up to ~100 mm)** can come out too thick: depth noise sets the
thinnest wall the fusion can resolve.

**A hole is marked *rough*** — it was only seen at an angle (typical for holes in side walls), so its outline is
ragged and its Ø is too small. Rescan with a few views looking straight into it, or measure it directly.

**CUDA / RAM out of memory** — choose *Fast*, or lower `n_keyframes` / `promptda_size` in `partscan/config.py`.

## What accuracy to expect — honestly
The pipeline was tested end-to-end on **synthetic iPhone scans** of a known 60 × 40 × 30 mm L-bracket: 1920×1440
video, ARKit-like tracking with drift and a 1.2 % scale error, lens distortion, blurred / noisy / biased LiDAR, and
the capture described in the guide. The two AI models could not be run in the test environment, so they were
**simulated** by stand-ins with deliberately injected errors (depth: ±1 % scale and ±3 mm offset per frame, a smooth
±0.6 mm error field, noise, blurred edges; outlines: ±1 px jitter plus occasional gross mistakes). With the board:

| feature | true (mm) | scanned (mm) | error (mm) |
|---|---|---|---|
| length × width × height (between faces) | 60 × 40 × 30 | 59.97 × 39.97 × 29.94 | −0.03 / −0.03 / −0.06 |
| outer envelope (includes scan noise) | 60 × 40 × 30 | 60.47 × 40.41 × 30.07 | +0.47 / +0.41 / +0.07 |
| wall / base thickness | 6 / 8 | 5.97 / 7.97 | −0.03 / −0.03 |
| inside length / wall height above base | 54 / 22 | 53.99 / 21.95 | −0.01 / −0.05 |
| 2 × Ø8 vertical holes | 8 | 7.95 / 7.94 | −0.05 / −0.06 |
| Ø6 side hole, filmed from the rings only | 6 | ≈5.15, marked *rough* | −0.85 |
| Ø6 side hole, second scan with a few low views straight into it | 6 | 5.74 | −0.26 |
| volume | 23.51 cm³ | 23.23 cm³ | −1.2 % |
| whole surface vs. truth | | mean 0.06, 95 % within 0.16 | |

Without the board, sizes came out about 1 % too large (the phone's own scale error): enter one caliper value and
tick *Rescale*. When the depth stand-in was made to flatten the part's relief (by up to 6 %, a typical failure
on small, dark or shiny parts), the height came out 1.3 mm short and the volume 8.6 % low — and the report caught
it: the photo outlines were 7 % larger than the model (flagged red) and the app showed a warning.

So the geometry engine can reach a few hundredths to a tenth of a millimetre **when the AI models behave like the
stand-ins**. Real parts add reflections, surface finish, lighting and the real models' own errors; nothing here has
been validated on real captures yet. Treat **±0.2–0.5 mm on a 50–100 mm part** as a plausible target with the board,
and **check one dimension with a caliper on every part** (*Caliper check* shows the real error of that scan).
This is a measurement aid, not a replacement for a CMM.

## Hosting it yourself (permanent phone app)
The same app runs unchanged on a **Hugging Face Space** with a GPU, which gives it a permanent address (no notebook
to keep open, no expiring link): create a Space (SDK *Gradio*), upload the contents of `PartScan_HF_Space.zip`
(delivered with this notebook; it is just `partscan/` + `app.py` + `requirements.txt` + `README.md`), and pick
**Nvidia T4 small** under *Settings → Hardware* ($0.40 / h as of Oct 2026, billed only while running; set a
*sleep time* so it stops when idle — waking up takes 1–2 min). Make the Space **private** unless you want the whole
internet to use your GPU. On the phone: open the Space's URL in Safari → *Share → Add to Home Screen*.

**Licences.** Prompt Depth Anything weights: Apache-2.0 (trained on Hypersim / ScanNet++ / ARKitScenes — check those
dataset terms before commercial use). SAM 2.1: Apache-2.0. Stray Scanner: MIT. three.js: MIT.
"""


def main():
    nb = nbf.v4.new_notebook()
    cells = [nbf.v4.new_markdown_cell(INTRO), nbf.v4.new_code_cell(SETUP), nbf.v4.new_markdown_cell(ENGINE_MD)]
    for mod in MODULES:
        src = open(os.path.join(PKG, mod + ".py"), encoding="utf-8").read().rstrip() + "\n"
        cells.append(nbf.v4.new_code_cell(f"%%writefile /content/partscan/{mod}.py\n" + src))
    cells += [nbf.v4.new_markdown_cell(DRIVE_MD), nbf.v4.new_code_cell(LAUNCH),
              nbf.v4.new_markdown_cell(MANUAL_MD), nbf.v4.new_code_cell(MANUAL),
              nbf.v4.new_markdown_cell(TIPS)]
    nb["cells"] = cells
    nb["metadata"] = {"accelerator": "GPU", "colab": {"provenance": [], "gpuType": "T4", "toc_visible": True,
                                                      "name": "PartScan_LiDAR.ipynb"},
                      "kernelspec": {"name": "python3", "display_name": "Python 3"},
                      "language_info": {"name": "python"}}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    nbf.write(nb, OUT)
    print("wrote", OUT, os.path.getsize(OUT) // 1024, "KB,", len(cells), "cells")


if __name__ == "__main__":
    main()
