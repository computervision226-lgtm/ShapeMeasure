"""Small REST API for the PartScan phone app, attached to the running Gradio server.

    POST /api/scans                     multipart: file (scan .zip / .r3d) + options  -> {jobId}
    GET  /api/jobs                      recent jobs
    GET  /api/jobs/{id}                 status, progress, and (when done) summary, rows, warnings, files
    GET  /api/jobs/{id}/files/{key}     a result file (zip, stl, obj, ply, glb, drawing, qa, csv, json, report, viewer)
    GET  /api/jobs/{id}/raw/{name}      any file of the result folder by name (lets report.html find its images)
    GET  /api/ping                      {ok, app, version, auth, device}

Authentication (optional): an access key checked on every /api route, given as the X-PartScan-Key
header, a ?k= query parameter, or the partscan_key cookie (set after the first keyed request so an
HTML report's images load too).
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import queue
import secrets
import shutil
import threading
import time
import traceback
import uuid

# module level on purpose: with postponed annotations FastAPI resolves the endpoint
# signatures against this module's globals
from fastapi import File, Form, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402

log = logging.getLogger("partscan")
FILE_KEYS = ("zip", "stl", "obj", "ply", "glb", "drawing", "qa", "csv", "json", "report", "viewer")


class Job:
    def __init__(self, job_id: str, out_dir: str, options: dict):
        self.id = job_id
        self.dir = out_dir
        self.options = options
        self.status = "queued"          # queued | running | done | error
        self.progress = 0.0
        self.message = "waiting"
        self.error = None
        self.created = time.time()
        self.finished = None
        self.result = None              # dict, see _build_result

    def to_dict(self, full: bool = True) -> dict:
        d = dict(jobId=self.id, status=self.status, progress=round(float(self.progress), 4), message=self.message,
                 error=self.error, createdAt=datetime.datetime.fromtimestamp(self.created).isoformat(timespec="seconds"),
                 options=self.options)
        if self.result:
            d.update(self.result if full else dict(summary=self.result.get("summary")))
        return d


def _build_result(res, job_dir: str) -> dict:
    from .export import measurement_rows
    from .measure import best_dims
    from .report import quality_rows
    L, W, H, how = best_dims(res.measurements)
    holes = [c for c in res.measurements.get("circles", []) if c["kind"] == "hole"]
    files = {k: os.path.basename(v) for k, v in res.files.items() if k in FILE_KEYS and v and os.path.exists(v)}
    return dict(
        summary=dict(length=round(L, 3), width=round(W, 3), height=round(H, 3), how=how, holes=len(holes),
                     roughHoles=sum(1 for c in holes if c.get("rough")),
                     scaleReference=res.quality.get("scale_reference", ""), keyframes=res.quality.get("keyframes"),
                     seconds=round(float(res.seconds), 1)),
        rows=[[str(a), str(b), str(c)] for a, b, c in measurement_rows(res.measurements)],
        quality=[[str(a), str(b), str(s)] for a, b, s in quality_rows(res.quality)],
        warnings=list(res.warnings),
        files=files,
    )


class JobManager:
    """Runs reconstructions one after another on a background thread."""

    def __init__(self, out_root: str, run_fn=None):
        self.out_root = out_root
        self.jobs: dict[str, Job] = {}
        self.q: queue.Queue = queue.Queue()
        self.run_fn = run_fn                 # for tests: replaces pipeline.run
        os.makedirs(out_root, exist_ok=True)
        self._load_finished()
        self.thread = threading.Thread(target=self._worker, daemon=True, name="partscan-jobs")
        self.thread.start()

    # -- persistence of finished jobs (so the phone can list earlier results after a restart)
    def _load_finished(self):
        for name in sorted(os.listdir(self.out_root)):
            jf = os.path.join(self.out_root, name, "job.json")
            if os.path.exists(jf):
                try:
                    d = json.load(open(jf))
                    j = Job(d["jobId"], os.path.join(self.out_root, name), d.get("options", {}))
                    j.status, j.progress, j.message, j.error = d["status"], 1.0, d.get("message", ""), d.get("error")
                    j.created = d.get("created", os.path.getmtime(jf))
                    j.result = d.get("result")
                    self.jobs[j.id] = j
                except Exception as ex:
                    log.warning("could not load %s: %s", jf, ex)

    def _save(self, job: Job):
        with open(os.path.join(job.dir, "job.json"), "w") as fh:
            json.dump(dict(jobId=job.id, status=job.status, message=job.message, error=job.error, created=job.created,
                           options=job.options, result=job.result), fh)

    def submit(self, scan_path: str, options: dict) -> Job:
        job_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        job_dir = os.path.join(self.out_root, job_id)
        os.makedirs(job_dir, exist_ok=True)
        job = Job(job_id, job_dir, options)
        job.scan_path = scan_path
        self.jobs[job_id] = job
        self.q.put(job)
        return job

    def _worker(self):
        while True:
            job = self.q.get()
            try:
                self._run(job)
            except Exception as ex:
                log.error(traceback.format_exc())
                job.status, job.error, job.message = "error", f"{type(ex).__name__}: {ex}", "failed"
            finally:
                job.finished = time.time()
                try:
                    self._save(job)
                except Exception as ex:
                    log.warning("could not save job: %s", ex)
                self.q.task_done()

    def _run(self, job: Job):
        from . import pipeline
        from .config import board_from_check, make_config
        o = job.options
        board, note = board_from_check(o.get("board_type", ""), float(o.get("check_mm") or 0), o.get("check_on", ""))
        cfg = make_config(o.get("preset", "Accurate (recommended)"), use_board=bool(o.get("use_board", True)),
                          board=board, material=o.get("material", "Steel"), reference_name=o.get("ref_name", "") or "",
                          reference_mm=float(o.get("ref_mm") or 0), apply_reference_scale=bool(o.get("ref_apply", False)))
        job.status, job.message = "running", "waiting for the GPU"

        def prog(p, m):
            job.progress, job.message = float(p), str(m)

        with pipeline.RUN_LOCK:
            job.message = "starting"
            res = (self.run_fn or pipeline.run)(job.scan_path, job.dir, cfg, progress=prog)
        if note and cfg.use_board:
            res.warnings.insert(0, note)
        job.result = _build_result(res, job.dir)
        job.status, job.progress, job.message = "done", 1.0, "done"
        try:
            os.remove(job.scan_path)        # the upload is big; the results folder keeps everything that matters
        except OSError:
            pass

    def list(self, limit: int = 50) -> list:
        jobs = sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)[:limit]
        return [j.to_dict(full=False) for j in jobs]

    def delete(self, job_id: str) -> bool:
        job = self.jobs.pop(job_id, None)
        if job is None:
            return False
        shutil.rmtree(job.dir, ignore_errors=True)
        return True


def new_key() -> str:
    return secrets.token_urlsafe(9)


def attach_api(app, out_root: str, api_key: str | None = None, manager: JobManager | None = None) -> JobManager:
    """Add the /api routes to the FastAPI app that Gradio's launch() returned."""
    from . import __version__
    manager = manager or JobManager(out_root)
    api_root = os.path.join(out_root, "api")
    os.makedirs(api_root, exist_ok=True)

    def check(request: Request):
        if not api_key:
            return False
        got = (request.headers.get("x-partscan-key") or request.query_params.get("k")
               or request.cookies.get("partscan_key"))
        if got != api_key:
            raise HTTPException(status_code=401, detail="missing or wrong access key")
        return request.cookies.get("partscan_key") != api_key   # True -> set the cookie on the response

    def with_cookie(response, set_cookie: bool):
        if set_cookie and api_key:
            response.set_cookie("partscan_key", api_key, httponly=True, samesite="lax", max_age=30 * 86400)
        return response

    def get_job(job_id: str) -> Job:
        job = manager.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="no such job")
        return job

    @app.get("/api/ping")
    def ping(request: Request):
        sc = check(request)
        from . import pipeline
        try:
            device = pipeline.resolve_device("auto")
        except Exception:
            device = "?"
        return with_cookie(JSONResponse(dict(ok=True, app="PartScan", version=__version__, auth=bool(api_key),
                                             device=device, jobsQueued=manager.q.qsize())), sc)

    @app.post("/api/scans")
    async def create_scan(request: Request, file: UploadFile = File(...), preset: str = Form("Accurate (recommended)"),
                          use_board: str = Form("1"), board_type: str = Form("Standard 8x6 (Letter / A4)"),
                          check_mm: str = Form("100"), check_on: str = Form("printed 100 mm ruler"),
                          material: str = Form("Steel"), ref_name: str = Form(""), ref_mm: str = Form("0"),
                          ref_apply: str = Form("0"), client: str = Form("")):
        sc = check(request)
        name = os.path.basename(file.filename or "scan.zip")
        ext = os.path.splitext(name)[1].lower() or ".zip"
        if ext not in (".zip", ".r3d"):
            raise HTTPException(status_code=400, detail="upload a .zip (Stray Scanner / PartScan / Polycam / 3DSA) or .r3d")
        tmp = os.path.join(api_root, f"upload-{uuid.uuid4().hex}{ext}")
        size = 0
        with open(tmp, "wb") as out:
            while True:
                chunk = await file.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                size += len(chunk)
        if size < 1000:
            os.remove(tmp)
            raise HTTPException(status_code=400, detail="the upload is empty")
        truthy = lambda v: str(v).strip().lower() in ("1", "true", "yes", "on")
        options = dict(preset=preset, use_board=truthy(use_board), board_type=board_type, check_mm=check_mm,
                       check_on=check_on, material=material, ref_name=ref_name, ref_mm=ref_mm, ref_apply=truthy(ref_apply),
                       client=client, upload=name, uploadBytes=size)
        job = manager.submit(tmp, options)
        log.info("job %s queued: %s (%.1f MB)", job.id, name, size / 1e6)
        return with_cookie(JSONResponse(job.to_dict(), status_code=202), sc)

    @app.get("/api/jobs")
    def list_jobs(request: Request):
        sc = check(request)
        return with_cookie(JSONResponse(dict(jobs=manager.list())), sc)

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str, request: Request):
        sc = check(request)
        return with_cookie(JSONResponse(get_job(job_id).to_dict()), sc)

    @app.delete("/api/jobs/{job_id}")
    def job_delete(job_id: str, request: Request):
        sc = check(request)
        job = get_job(job_id)
        if job.status in ("queued", "running"):
            raise HTTPException(status_code=409, detail="the job is still running")
        manager.delete(job_id)
        return with_cookie(JSONResponse(dict(ok=True)), sc)

    def serve(job: Job, name: str, set_cookie: bool):
        path = os.path.join(job.dir, name)
        if not name or "/" in name or "\\" in name or name.startswith(".") or not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="no such file")
        media = {".html": "text/html", ".png": "image/png", ".jpg": "image/jpeg", ".json": "application/json",
                 ".csv": "text/csv", ".zip": "application/zip", ".glb": "model/gltf-binary",
                 ".stl": "model/stl", ".obj": "text/plain", ".ply": "application/octet-stream"}
        return with_cookie(FileResponse(path, media_type=media.get(os.path.splitext(name)[1].lower(),
                                                                   "application/octet-stream"),
                                        filename=None if name.endswith(".html") else name), set_cookie)

    @app.get("/api/jobs/{job_id}/files/{key}")
    def job_file(job_id: str, key: str, request: Request):
        sc = check(request)
        job = get_job(job_id)
        if not job.result or key not in job.result.get("files", {}):
            raise HTTPException(status_code=404, detail="no such file")
        return serve(job, job.result["files"][key], sc)

    @app.get("/api/jobs/{job_id}/raw/{name}")
    def job_raw(job_id: str, name: str, request: Request):
        sc = check(request)
        return serve(get_job(job_id), name, sc)

    log.info("PartScan API attached (%s)", "access key required" if api_key else "no access key")
    return manager
