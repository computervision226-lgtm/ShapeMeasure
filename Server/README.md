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

Uploaded scans and results are kept only in the Space's temporary storage.
