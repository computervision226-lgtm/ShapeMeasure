"""PartScan on a Hugging Face Space (Gradio SDK). Pick a GPU under Settings -> Hardware (T4 small is enough)."""
import logging
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
OUT = os.environ.get("PARTSCAN_OUT", "/tmp/partscan_runs")
os.makedirs(OUT, exist_ok=True)

from partscan import pipeline  # noqa: E402
from partscan.app import build_app, launch  # noqa: E402
from partscan.config import make_config  # noqa: E402

if os.environ.get("PARTSCAN_PRELOAD", "1") == "1":
    try:                                   # load the AI models once at start-up (first scan is then faster)
        dev = pipeline.resolve_device("auto")
        cfg0 = make_config()
        pipeline.get_depth_model(cfg0, dev)
        pipeline.get_segmenter(cfg0, dev)
        logging.info("models ready on %s", dev)
    except Exception as ex:                # no GPU / no network yet: they load on the first run instead
        logging.warning("models not preloaded: %s", ex)

from partscan.api import attach_api  # noqa: E402

auth = None
if os.environ.get("PARTSCAN_UI_PASSWORD"):      # optional login for the web page (Space secrets)
    auth = (os.environ.get("PARTSCAN_UI_USER", "partscan"), os.environ["PARTSCAN_UI_PASSWORD"])
demo = build_app(OUT, drive_dir=None)
app, local_url, share_url = launch(demo, OUT, prevent_thread_lock=True, auth=auth)
attach_api(app, OUT, api_key=os.environ.get("PARTSCAN_API_KEY") or None)   # phone-app API; key = Space secret
demo.block_thread()
