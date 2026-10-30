---
title: PartScan
emoji: 📐
colorFrom: orange
colorTo: gray
sdk: gradio
sdk_version: 6.29.0
python_version: "3.12"
app_file: app.py
suggested_hardware: t4-small
pinned: false
---

# PartScan — iPhone LiDAR → measured 3D model of a part

Upload a Stray Scanner recording (zip) of one small part on the printed ChArUco board and get a true-scale mesh
(STL/OBJ/PLY/GLB), measurements, dimensioned drawings, an interactive measuring viewer and a report.

**Hardware:** choose *Nvidia T4 small* (or better) under *Settings → Hardware*; CPU-only runs take hours.
Set a *sleep time* so the Space stops when idle. Keep the Space **private** unless you want it public.

**On the iPhone:** open the Space URL in Safari → *Share → Add to Home Screen*.
Scans live in *Files → On My iPhone → Stray Scanner*; long-press a scan folder → *Compress* → upload the zip here.

Every scan is logged (`scan_log.csv`, one folder per scan with the raw upload, results and `pipeline.log`; browse
them in the *Scans log* tab). Without persistent storage this lives in the Space's temporary disk and is lost on
restart; with persistent storage set the secret `PARTSCAN_ARCHIVE_DIR=/data`. `PARTSCAN_KEEP_SCANS=0` drops the
raw uploads after processing.
