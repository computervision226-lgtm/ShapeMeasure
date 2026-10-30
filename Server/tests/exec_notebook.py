"""Execute the delivered notebook headless (Colab-mirror venv), with the AI model loaders swapped
for offline oracles, the UI launched and closed, and the 'Run on a file' cell processing a scan.

python exec_notebook.py NOTEBOOK SYNTH_DIR OUT_DIR
"""
import os
import sys
import time

import nbformat
from nbclient import NotebookClient

NB, SYNTH, OUT = sys.argv[1:4]
os.makedirs(OUT, exist_ok=True)
nb = nbformat.read(NB, as_version=4)
tests_dir = os.path.dirname(os.path.abspath(__file__))

for c in nb.cells:
    if c.cell_type != "code":
        continue
    if c.source.startswith("#@title 3 · Start the app"):
        c.source = (c.source.replace("USE_GOOGLE_DRIVE = True", "USE_GOOGLE_DRIVE = False")
                    .replace("PRELOAD_AI_MODELS = True", "PRELOAD_AI_MODELS = False")
                    .replace("launch(demo, OUT, share=PUBLIC_LINK)",
                             "launch(demo, OUT, share=PUBLIC_LINK)\nimport time; time.sleep(3); demo.close()"))
    if c.source.startswith("#@title 4 · Run on a file"):
        c.source = c.source.replace('SCAN_PATH = "/content/drive/MyDrive/PartScan/my_scan.zip"',
                                    f'SCAN_PATH = "{os.path.join(SYNTH, "stray_scan.zip")}"')

inject = f'''# TEST ONLY: offline stand-ins for the AI models + a faster CPU configuration
import sys; sys.path.insert(0, {tests_dir!r})
from oracles import OracleDepth, OracleSegmenter
from partscan import pipeline, config
truth = {os.path.join(SYNTH, "truth.npz")!r}
pipeline.get_depth_model = lambda cfg, dev: OracleDepth(truth)
pipeline.get_segmenter = lambda cfg, dev: OracleSegmenter(truth)
config.PRESETS["Accurate (recommended)"].update(n_keyframes=45, max_candidates=150, sift_features=3000, device="cpu")
'''
idx = next(i for i, c in enumerate(nb.cells) if c.cell_type == "code" and c.source.startswith("#@title 4 · Run on a file"))
nb.cells.insert(idx, nbformat.v4.new_code_cell(inject))

t0 = time.time()
client = NotebookClient(nb, timeout=3000, kernel_name="python3", resources={"metadata": {"path": OUT}})
try:
    client.execute()
    ok = True
except Exception as ex:
    ok = False
    print("EXECUTION ERROR:", type(ex).__name__, str(ex)[:3000])
nbformat.write(nb, os.path.join(OUT, "executed.ipynb"))
print(f"executed in {time.time() - t0:.0f}s ok={ok}")
for i, c in enumerate(nb.cells):
    if c.cell_type != "code":
        continue
    for o in c.get("outputs", []):
        if o.get("output_type") == "error":
            print(f"cell {i} ERROR {o['ename']}: {o['evalue'][:500]}")
        elif o.get("output_type") == "stream" and i in (1, len(nb.cells) - 2, len(nb.cells) - 3):
            print(f"cell {i} {o['name']}: {o['text'][-1500:]}")
