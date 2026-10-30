"""Re-measure an exported mesh with the current code and score it against ground truth.
python rescore.py SYNTH_DIR RUN_DIR"""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import numpy as np, trimesh
from partscan.measure import measure_all, best_dims
from run_synthetic import surface_deviation
syn, run = sys.argv[1], sys.argv[2]
gt = json.load(open(os.path.join(syn, "truth.json"))); tr = np.load(os.path.join(syn, "truth.npz"))
m = trimesh.load(os.path.join(run, "part_mm.stl"))
meas = measure_all(m, "Steel")
L, W, H, how = best_dims(meas)
o = meas["overall"]
print(f"envelope {o['length_x']:.3f} x {o['width_y']:.3f} x {o['height_z']:.3f} | best ({how}) {L:.3f} x {W:.3f} x {H:.3f}"
      f" | truth {gt['length_mm']} x {gt['width_mm']} x {gt['height_mm']}")
print("between faces:", {k: round(v, 3) for k, v in o.get("between_faces", {}).items()})
print("holes:", [(c["axis"], round(c["diameter"], 3), round(c["diameter_spread"], 2), c["n_slices"], "rough" if c.get("rough") else "clean") for c in meas["circles"] if c["kind"] == "hole"])
print("planes:", [(r["kind"], round(r["distance"], 3)) for r in meas["plane_relations"]])
print("volume", round(meas["mass"]["volume_mm3"]), "truth", round(gt["volume_mm3"]), f"({(meas['mass']['volume_mm3']/gt['volume_mm3']-1)*100:+.2f}%) watertight", meas["mass"]["watertight"])
print("surface:", {k: round(v, 3) for k, v in surface_deviation(m, tr["part_local_vertices"], tr["part_faces"]).items()})
