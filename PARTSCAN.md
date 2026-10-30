# PartScan — iPhone LiDAR → measured 3D model (TestFlight via Codemagic)

PartScan is two pieces:

| Piece | Where | What it does |
|---|---|---|
| **iPhone app** (`PartScan/`, `PartScan.xcodeproj`) | TestFlight, built by Codemagic | Records video + LiDAR depth + camera poses of a part on the printed board, guides the capture (three rings + top pass), uploads the scan and shows the results: dimensions, holes, wall thicknesses, drawings, STL/OBJ, interactive 3D viewer |
| **Server** (`Server/`, `Notebook/`) | A Colab notebook with a T4 GPU, or a Hugging Face Space | Board-based scale and camera poses, photogrammetry, Prompt Depth Anything, SAM 2.1, TSDF fusion, measurement. The app talks to it through a small REST API (`Server/partscan/api.py`) |

The phone only records; the AI models and the 3D fusion need a GPU, so they run on the server.
Only iPhones with LiDAR work: **iPhone 12 Pro → 17 Pro / Pro Max** (not the plain iPhone 17 or the iPhone Air).

## 1 · Build the app with Codemagic and install it from TestFlight

The repository already contains a working Codemagic setup for ShapeMeasure; PartScan reuses it.

1. **Apple**: nothing to register. PartScan uses the existing ShapeMeasure bundle ID `com.maged.shapemeasure`,
   so its builds appear under the **ShapeMeasure** app in App Store Connect / TestFlight as version **1.0.x**
   (ShapeMeasure's own builds are 0.1.x). On the phone the two are the same app slot: installing a PartScan build
   replaces ShapeMeasure, and installing a ShapeMeasure build replaces PartScan. *(To have both installed at once,
   register a second App ID, e.g. `com.maged.partscan`, create its app record, and put it in both bundle-ID fields
   of the `partscan-testflight` workflow.)*
2. **One secret for signing** (once). The workflow fetches — or creates — the Apple Distribution certificate and
   the App Store provisioning profile by itself through the `shapemeasure-apple` App Store Connect key. Apple
   certificates need a private key that Codemagic does not hold, so make one and store it as a secret:
   - On a Mac / Linux terminal, or Windows PowerShell:
     `ssh-keygen -t rsa -b 2048 -m PEM -f partscan_cert_key -q -N ""` → this writes the file `partscan_cert_key`.
   - Codemagic → the ShapeMeasure app → **Environment variables** tab: name `CERTIFICATE_PRIVATE_KEY`, value =
     the whole content of `partscan_cert_key` (from `-----BEGIN RSA PRIVATE KEY-----` to the END line), group
     `partscan_signing` (type the name to create it), tick **Secure**, Add.
   - The App Store Connect key behind the integration needs the *App Manager* (or Admin) role. If the build
     reports a permission error while creating the bundle ID, register `com.maged.shapemeasure` once in
     [Apple Developer → Identifiers](https://developer.apple.com/account/resources/identifiers/list) and rerun.
   - If Apple answers that the certificate limit is reached, revoke an unused *Apple Distribution* certificate in
     the Developer portal (Certificates) and rerun.
3. **Start a build**: Codemagic → *Start new build* → this branch → workflow **PartScan TestFlight**. The build
   takes ~10 min and uploads the signed IPA to App Store Connect. *If the Swift compile fails*: open the log, copy
   the first `error:` line with its file/line, and send it back — the code was written without a Mac, so the first
   cloud build is its first compile.
4. **TestFlight**: App Store Connect → ShapeMeasure → TestFlight → the **1.0.0** build → add it to your internal
   group (or turn on **Enable automatic distribution** on the group so every new build is pushed by itself). In the
   [TestFlight](https://apps.apple.com/app/testflight/id899247664) app on the iPhone, pick the 1.0.0 build and
   install — the icon and name on the phone are PartScan's.
   (`BUILD_NUMBER_OFFSET` in the workflow: raise it if Apple complains about a duplicate build number.)

## 2 · Start a server

**Option A — Colab (quickest, ~free):** open `Notebook/PartScan_LiDAR_Colab.ipynb` in Colab (small; pulls the
latest code from this repository each run — `PartScan_LiDAR.ipynb` is the self-contained version), GPU runtime,
set `PUBLIC_LINK = True` in section 3, *Runtime → Run all*. The cell prints a **QR code** holding the public link and the
access key. Keep the notebook tab open while scanning; the link lasts up to a week. Google allows this with Colab
Pro / a compute-unit balance (its FAQ disallows using a *free* runtime mainly through a web link).

**Option B — Hugging Face Space (permanent address):** create a Space (SDK *Gradio*), upload the contents of
`Server/` (`app.py`, `requirements.txt`, `README.md`, `partscan/`), choose **Nvidia T4 small** under *Settings →
Hardware* ($0.40/h while running; set a *sleep time*). Add the Space secret `PARTSCAN_API_KEY` (any long random
string) — that is the access key the app sends — and optionally `PARTSCAN_UI_PASSWORD` to put a login on the web
page. In the app, type the Space URL (`https://<user>-<space>.hf.space`) and the key. A *private* Space also
needs your Hugging Face token in the app (read access).

## 3 · Use the app

1. **Server tab** → *Scan the QR code from the notebook* (or type URL + key) → *Test connection* should say
   "Connected … on cuda".
2. Print a board at 100 %, glue it flat, measure its 100 mm ruler. The PDFs are in `Boards/` here, and the
   notebook also writes them to Google Drive (*MyDrive/ShapeMeasure/Boards*, or *MyDrive/PartScan/Boards*) when it starts.
3. **Scan tab**: part in the middle of the board, 30–40 cm away. Record: low ring (≈20°), middle ring (≈45°),
   high ring (≈70°), slow pass over the top; a few views straight into any side holes. The panel shows how much of
   each ring is covered. Stop.
4. **Upload & build**: enter the print-check value (and a caliper reference if you have one), upload (200–400 MB
   on Wi-Fi), wait ~10 min. The result shows length × width × height, holes, wall thicknesses, the drawing, the
   measurement table, quality checks, the interactive 3D viewer and downloads (STL/OBJ/PLY/GLB, report).
5. Scans are kept under *Files → On My iPhone → PartScan* too, so a scan can also be moved to *Google Drive ›
   PartScan* and processed from the notebook's own interface.

## Accuracy (honest)

The reconstruction pipeline was validated on synthetic iPhone captures (see the notebook's last section): between
flat faces ±0.03–0.06 mm, Ø8 holes within 0.06 mm, a side hole 0.26 mm small when filmed straight on; the AI models
were simulated by stand-ins and nothing has been measured on a real capture yet. Expect ±0.2–0.5 mm on real
50–100 mm parts with the board, and always check one dimension with a caliper.

## Layout

```
PartScan/              SwiftUI app (iOS 17+): ScanRecorder (ARKit → Stray Scanner layout), API client, views
PartScan.xcodeproj     generated by Scripts/generate_partscan_project.py (the workflow regenerates it)
Server/                the reconstruction server: app.py (Gradio UI + REST API), partscan/ package, tests/
Notebook/              build_notebook.py → PartScan_LiDAR.ipynb (self-contained) and, with --slim, PartScan_LiDAR_Colab.ipynb
Boards/                printable ChArUco boards (Letter, A4, A3, Tabloid; Letter without text)
codemagic.yaml         workflows: shapemeasure-testflight (existing app) and partscan-testflight
```

Scan format written by the app (the Stray Scanner 1.4 layout the server reads): `rgb.mp4` (HEVC, sensor
orientation), `depth/000000.png…` (16-bit, millimetres), `confidence/…` (0/1/2), `odometry.csv` (camera-to-world,
OpenCV camera axes, per-frame fx fy cx cy), `camera_matrix.csv`, `imu.csv`, `partscan.json`.
