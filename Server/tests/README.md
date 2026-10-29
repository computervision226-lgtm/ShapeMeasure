# Server tests (no GPU, no model weights needed)

* `synth_scan.py OUT --fps 6 --seconds 55 [--side-views]` – renders a synthetic iPhone scan (Stray Scanner layout) of a known
  60 × 40 × 30 mm bracket on the board, with realistic tracking, lens and LiDAR errors, plus `truth.json/npz`.
* `oracles.py` – stand-ins for Prompt Depth Anything and SAM 2.1 with injected errors (used because the real models need a GPU).
* `run_synthetic.py SYNTH OUT --mode oracle --keyframes 70` – full pipeline + scoring against the truth.
* `rescore.py`, `fusion_bias.py` – re-measure an exported mesh / fusion ablations.
* `ui_e2e.py` – launches the Gradio app with the oracles and drives it in a headless browser (needs playwright).
* `exec_notebook.py NOTEBOOK SYNTH OUT` – executes the Colab notebook headless with the oracles injected.

Needs: the `Server/requirements.txt` packages plus `open3d` (rendering the synthetic scans) and, for the UI test, `playwright`.
