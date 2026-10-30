"""End-to-end UI test: launch the Gradio app (AI models swapped for oracles), upload a synthetic
scan with a headless browser, press Build, wait for the results and screenshot desktop + phone.

python ui_e2e.py SYNTH_DIR OUT_DIR
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SYNTH, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)

from oracles import OracleDepth, OracleSegmenter  # noqa: E402
from partscan import pipeline  # noqa: E402

truth = os.path.join(SYNTH, "truth.npz")
pipeline.get_depth_model = lambda cfg, dev: OracleDepth(truth)
pipeline.get_segmenter = lambda cfg, dev: OracleSegmenter(truth)
_orig_run = pipeline.run


def _run(path, out, cfg, progress=None, **kw):
    cfg.device = "cpu"
    cfg.n_keyframes = 45
    cfg.max_candidates = 150
    cfg.sift_features = 3000
    return _orig_run(path, out, cfg, progress=progress, **kw)


pipeline.run = _run

from partscan.app import build_app, launch  # noqa: E402

runs = os.path.join(OUT, "runs")
demo = build_app(runs, drive_dir=None)
import gradio as gr  # noqa: E402

demo.launch(server_port=7865, prevent_thread_lock=True, allowed_paths=[runs], quiet=True, ssr_mode=False,
            theme=gr.themes.Soft(primary_hue="orange", neutral_hue="slate"))

from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    pg = b.new_page(viewport={"width": 1280, "height": 900})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://127.0.0.1:7865/", wait_until="networkidle")
    pg.screenshot(path=os.path.join(OUT, "ui_empty.png"), full_page=True)
    pg.locator("input[type=file]").first.set_input_files(os.path.join(SYNTH, "stray_scan.zip"))
    pg.wait_for_timeout(3000)
    pg.get_by_role("button", name="Build 3D model").click()
    t0 = time.time()
    while time.time() - t0 < 1500:
        if pg.locator("iframe").count() > 0:
            break
        if pg.locator(".toast-body, .error").count() > 0:
            print("ERROR toast:", pg.locator(".toast-body, .error").first.inner_text()[:500])
            break
        time.sleep(5)
    print("finished after", int(time.time() - t0), "s")
    pg.wait_for_timeout(6000)
    pg.screenshot(path=os.path.join(OUT, "ui_result.png"), full_page=True)
    # phone layout
    ctx = b.new_context(viewport={"width": 402, "height": 874}, device_scale_factor=2, is_mobile=True, has_touch=True)
    ph = ctx.new_page()
    ph.goto("http://127.0.0.1:7865/", wait_until="networkidle")
    ph.screenshot(path=os.path.join(OUT, "ui_phone.png"), full_page=True)
    ph.get_by_role("tab", name="2 · Board").click()
    ph.get_by_role("button", name="Create board PDF").click()
    ph.wait_for_timeout(4000)
    ph.screenshot(path=os.path.join(OUT, "ui_phone_board.png"), full_page=True)
    print("page errors:", errs[:5])
    b.close()
demo.close()
