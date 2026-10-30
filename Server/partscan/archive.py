"""Scan archive: everything needed to debug a scan later, in one folder per job plus one CSV log.

    <archive>/Scans/<job_id>/   scan.zip (the raw upload)  ·  pipeline.log  ·  job.json (options, capture info,
                               result summary, quality metrics, warnings, error + traceback)  ·  all result files
    <archive>/scan_log.csv      one row per scan (opens in Google Sheets)

On Colab the archive lives on Google Drive (MyDrive/ShapeMeasure or MyDrive/PartScan), so it survives the runtime.
"""
from __future__ import annotations

import csv
import datetime
import json
import logging
import os
import shutil
import threading
import zipfile

log = logging.getLogger("partscan")

QUALITY_KEYS = ("scale_reference", "keyframes", "frames_total", "board_frames", "board_ba_rms_px", "arkit_scale_error_pct",
                "k1", "feature_points", "ba_rms_tracks_px", "silhouette_iou_median", "silhouette_iou_p10",
                "outline_to_model_area_ratio", "depth_fit_sigma_mm", "depth_to_surface_rms_mm", "depth_frames_corrected",
                "fusion_voxel_mm", "fusion_observed_fraction", "depth_model", "segmenter", "device", "seconds")

CSV_COLUMNS = ["time", "job_id", "source", "status", "client", "device", "system", "app_version", "upload", "upload_mb",
               "frames", "recorded_s", "fps_stride", "dropped", "coverage", "preset", "board", "check_mm", "material",
               "ref_name", "ref_mm", "length", "width", "height", "how", "holes", "rough_holes",
               "scale_reference", "keyframes", "board_frames", "board_ba_px", "feature_points", "tracks_ba_px",
               "silhouette_iou", "outline_ratio", "depth_fit_sigma_mm", "depth_to_surface_rms_mm", "voxel_mm",
               "processing_s", "warnings", "error", "folder"]


def capture_info(scan_path: str) -> dict:
    """The partscan.json the iPhone app writes into every scan (device, frames, coverage...), if present."""
    try:
        if os.path.isdir(scan_path):
            for root, _dirs, files in os.walk(scan_path):
                if "partscan.json" in files:
                    return json.load(open(os.path.join(root, "partscan.json")))
            return {}
        if zipfile.is_zipfile(scan_path):
            with zipfile.ZipFile(scan_path) as z:
                for n in z.namelist():
                    if n.endswith("partscan.json") and n.count("/") <= 2:
                        return json.loads(z.read(n).decode("utf-8", "replace"))
    except Exception as ex:
        log.warning("could not read the capture info: %s", ex)
    return {}


class JobLog:
    """Captures everything the pipeline logs during one job into <job_dir>/pipeline.log."""

    def __init__(self, job_dir: str):
        os.makedirs(job_dir, exist_ok=True)
        self.path = os.path.join(job_dir, "pipeline.log")
        self.handler = logging.FileHandler(self.path, encoding="utf-8")
        self.handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
        self.handler.setLevel(logging.INFO)

    def __enter__(self):
        log.addHandler(self.handler)
        return self

    def __exit__(self, *exc):
        log.removeHandler(self.handler)
        self.handler.close()
        return False


class Archive:
    def __init__(self, out_root: str, archive_dir: str | None = None, keep_scans: bool = True):
        self.out_root = out_root
        self.archive_dir = archive_dir          # e.g. /content/drive/MyDrive/ShapeMeasure; None = only out_root
        self.keep_scans = keep_scans
        self.lock = threading.Lock()
        for d in (out_root, archive_dir):
            if d:
                os.makedirs(d, exist_ok=True)

    @property
    def csv_path(self) -> str:
        return os.path.join(self.archive_dir or self.out_root, "scan_log.csv")

    @property
    def scans_dir(self) -> str:
        return os.path.join(self.archive_dir or self.out_root, "Scans")

    def describe(self) -> str:
        where = self.archive_dir or self.out_root
        return f"{where} (raw scans {'kept' if self.keep_scans else 'not kept'})"

    # ------------------------------------------------------------------ one finished (or failed) job
    def record(self, job_id: str, job_dir: str, source: str, options: dict, capture: dict, scan_path: str | None,
               res=None, error: str | None = None, tb: str | None = None, seconds: float | None = None,
               scan_keep: str = "move") -> dict:
        """Write job.json, keep the raw scan, append the CSV row and mirror the folder to the archive.
        scan_keep: "move" = the file is a temporary upload, move it into the job folder (or delete it when raw
        scans are not kept); "reference" = the scan already lives somewhere permanent (Google Drive, an archived
        job), just remember where. Returns the job record (dict)."""
        record = self.build_record(job_id, source, options, capture, scan_path, res, error, tb, seconds)
        try:
            if scan_path and os.path.exists(scan_path):
                if scan_keep == "reference" or os.path.isdir(scan_path):
                    record["scan_source"] = os.path.abspath(scan_path)
                elif self.keep_scans:
                    dst = os.path.join(job_dir, "scan" + (os.path.splitext(scan_path)[1].lower() or ".zip"))
                    if os.path.abspath(scan_path) != os.path.abspath(dst):
                        shutil.move(scan_path, dst)
                    record["scan_file"] = os.path.basename(dst)
                else:
                    os.remove(scan_path)
        except Exception as ex:
            log.warning("could not keep the raw scan: %s", ex)
        record["folder"] = self.target_folder(job_id, job_dir)
        with open(os.path.join(job_dir, "job.json"), "w") as fh:
            json.dump(record, fh, indent=1)
        record["folder"] = self.mirror(job_id, job_dir)
        try:
            self.append_csv(record)
        except Exception as ex:
            log.warning("could not append to %s: %s", self.csv_path, ex)
        return record

    def target_folder(self, job_id: str, job_dir: str) -> str:
        if self.archive_dir is None or os.path.abspath(os.path.dirname(job_dir)) == os.path.abspath(self.scans_dir):
            return job_dir
        return os.path.join(self.scans_dir, job_id)

    def build_record(self, job_id, source, options, capture, scan_path, res, error, tb, seconds) -> dict:
        from .api import _build_result
        rec = dict(jobId=job_id, source=source, status="error" if error else "done",
                   createdAt=datetime.datetime.now().isoformat(timespec="seconds"), created=datetime.datetime.now().timestamp(),
                   options=dict(options), capture=dict(capture or {}), error=error, traceback=tb,
                   message="failed" if error else "done", processingSeconds=seconds)
        if scan_path:
            try:
                rec["uploadBytes"] = (os.path.getsize(scan_path) if os.path.isfile(scan_path)
                                      else sum(os.path.getsize(os.path.join(r, f)) for r, _d, fs in os.walk(scan_path) for f in fs))
            except OSError:
                pass
        if res is not None:
            rec["result"] = _build_result(res, "")
            rec["quality"] = {k: res.quality.get(k) for k in QUALITY_KEYS if k in res.quality}
        return rec

    # ------------------------------------------------------------------ CSV
    def csv_row(self, rec: dict) -> dict:
        o, c, q = rec.get("options", {}), rec.get("capture", {}), rec.get("quality", {})
        s = (rec.get("result") or {}).get("summary", {})
        cov = c.get("coverage")
        f = lambda v, n=3: ("" if v is None else (round(v, n) if isinstance(v, float) else v))
        return dict(
            time=rec.get("createdAt", ""), job_id=rec.get("jobId", ""), source=rec.get("source", ""), status=rec.get("status", ""),
            client=o.get("client", ""), device=c.get("modelId") or c.get("device", ""), system=c.get("system", ""),
            app_version=c.get("appVersion", ""), upload=o.get("upload", ""),
            upload_mb=f((rec.get("uploadBytes") or 0) / 1e6, 1) if rec.get("uploadBytes") else "",
            frames=c.get("frames", ""), recorded_s=f(c.get("seconds"), 1), fps_stride=c.get("stride", ""), dropped=c.get("dropped", ""),
            coverage=("/".join(f"{int(round(100 * cov.get(k, 0)))}" for k in ("low", "mid", "high", "top")) if isinstance(cov, dict) else ""),
            preset=o.get("preset", ""), board=(o.get("board_type", "") if o.get("use_board", True) else "none"),
            check_mm=o.get("check_mm", ""), material=o.get("material", ""), ref_name=o.get("ref_name", ""), ref_mm=o.get("ref_mm", ""),
            length=s.get("length", ""), width=s.get("width", ""), height=s.get("height", ""), how=s.get("how", ""),
            holes=s.get("holes", ""), rough_holes=s.get("roughHoles", ""),
            scale_reference=q.get("scale_reference", ""), keyframes=q.get("keyframes", ""), board_frames=q.get("board_frames", ""),
            board_ba_px=f(q.get("board_ba_rms_px")), feature_points=q.get("feature_points", ""), tracks_ba_px=f(q.get("ba_rms_tracks_px")),
            silhouette_iou=f(q.get("silhouette_iou_median")), outline_ratio=f(q.get("outline_to_model_area_ratio")),
            depth_fit_sigma_mm=f(q.get("depth_fit_sigma_mm")), depth_to_surface_rms_mm=f(q.get("depth_to_surface_rms_mm")),
            voxel_mm=f(q.get("fusion_voxel_mm")), processing_s=f(rec.get("processingSeconds"), 1),
            warnings=" | ".join((rec.get("result") or {}).get("warnings", [])), error=rec.get("error") or "",
            folder=rec.get("folder", ""))

    def append_csv(self, rec: dict):
        row = self.csv_row(rec)
        with self.lock:
            new = not os.path.exists(self.csv_path)
            with open(self.csv_path, "a", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
                if new:
                    w.writeheader()
                w.writerow(row)

    def read_csv(self) -> list:
        if not os.path.exists(self.csv_path):
            return []
        with open(self.csv_path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    # ------------------------------------------------------------------ mirror to Drive
    def mirror(self, job_id: str, job_dir: str) -> str:
        """Copy the job folder into the archive (Drive). Returns the archived folder path."""
        dst = os.path.join(self.scans_dir, job_id)
        if self.archive_dir is None or os.path.abspath(os.path.dirname(job_dir)) == os.path.abspath(self.scans_dir):
            return job_dir
        try:
            os.makedirs(dst, exist_ok=True)
            for name in os.listdir(job_dir):
                src = os.path.join(job_dir, name)
                if name == "_work" or name.startswith("."):
                    continue
                out = os.path.join(dst, name)
                if os.path.isdir(src):
                    shutil.copytree(src, out, dirs_exist_ok=True)
                elif not os.path.exists(out) or os.path.getsize(out) != os.path.getsize(src):
                    shutil.copy2(src, out)
            return dst
        except Exception as ex:
            log.warning("could not archive %s to %s: %s", job_id, dst, ex)
            return job_dir

    def folders(self) -> dict:
        """job_id -> folder for every archived job (archive first, then the local run folder)."""
        out = {}
        for base in (self.scans_dir, self.out_root):
            if not os.path.isdir(base):
                continue
            for name in os.listdir(base):
                d = os.path.join(base, name)
                if os.path.isdir(d) and os.path.exists(os.path.join(d, "job.json")) and name not in out:
                    out[name] = d
        return out

    def load(self, job_id: str) -> tuple:
        """(record, folder) of an archived job, or (None, None)."""
        folder = self.folders().get(job_id)
        if not folder:
            return None, None
        try:
            return json.load(open(os.path.join(folder, "job.json"))), folder
        except Exception:
            return None, folder
