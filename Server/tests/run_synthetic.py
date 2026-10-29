"""Run the full pipeline on a synthetic scan and score it against ground truth.

python run_synthetic.py SYNTH_DIR OUT_DIR [--mode oracle|oracle-pess|lidar] [--keyframes 60] [--no-board-cfg]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import trimesh  # noqa: E402

from partscan import pipeline  # noqa: E402
from partscan.config import make_config  # noqa: E402


def surface_deviation(rec_mm: trimesh.Trimesh, gt_local_m: np.ndarray, gt_faces: np.ndarray):
    import open3d as o3d
    gt = trimesh.Trimesh(gt_local_m * 1000.0, gt_faces, process=False)
    gt_pts = gt.sample(60000)
    rec_pts = rec_mm.sample(60000)
    best = None
    for yaw in (0.0, np.pi):
        T0 = trimesh.transformations.rotation_matrix(yaw, (0, 0, 1))
        T0[:3, 3] = gt.bounds.mean(0) - (T0[:3, :3] @ rec_mm.bounds.mean(0))
        src = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(rec_pts))
        dst = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(gt_pts))
        dst.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=1.5, max_nn=30))
        reg = o3d.pipelines.registration.registration_icp(
            src, dst, 2.0, T0, o3d.pipelines.registration.TransformationEstimationPointToPlane(),
            o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=60))
        if best is None or reg.inlier_rmse < best.inlier_rmse or reg.fitness > best.fitness + 0.05:
            best = reg
    T = best.transformation
    rec_al = rec_pts @ T[:3, :3].T + T[:3, 3]

    def dist_to(V, F, pts):   # exact point-to-triangle distances, memory-light (open3d BVH)
        sc = o3d.t.geometry.RaycastingScene()
        sc.add_triangles(o3d.core.Tensor(np.asarray(V, np.float32)), o3d.core.Tensor(np.asarray(F, np.uint32)))
        return sc.compute_distance(o3d.core.Tensor(np.asarray(pts, np.float32))).numpy().astype(np.float64)

    d_rec_to_gt = dist_to(gt.vertices, gt.faces, rec_al)
    Vr = np.asarray(rec_mm.vertices) @ T[:3, :3].T + T[:3, 3]
    d_gt_to_rec = dist_to(Vr, rec_mm.faces, gt_pts[:20000])
    return dict(mean_mm=float(d_rec_to_gt.mean()), p95_mm=float(np.percentile(d_rec_to_gt, 95)),
                max_mm=float(d_rec_to_gt.max()), gt_to_rec_mean_mm=float(np.mean(d_gt_to_rec)),
                gt_to_rec_p95_mm=float(np.percentile(d_gt_to_rec, 95)), icp_fitness=float(best.fitness))


def evaluate(res, synth_dir):
    gt = json.load(open(os.path.join(synth_dir, "truth.json")))
    tr = np.load(os.path.join(synth_dir, "truth.npz"))
    m = res.measurements
    o = m["overall"]
    out = dict(L=o["length_x"] - gt["length_mm"], W=o["width_y"] - gt["width_mm"], H=o["height_z"] - gt["height_mm"])
    from partscan.measure import best_dims
    bL, bW, bH, how = best_dims(m)
    out.update(best_L=bL - gt["length_mm"], best_W=bW - gt["width_mm"], best_H=bH - gt["height_mm"], best_how=how)
    vol = m["mass"]["volume_mm3"]
    out["volume_pct"] = (vol / gt["volume_mm3"] - 1) * 100 if vol == vol else float("nan")
    holes = [c for c in m.get("circles", []) if c["kind"] == "hole"]
    hole_err = []
    for g in gt["holes"]:
        cands = [h for h in holes if abs(h["diameter"] - g["d_mm"]) < 1.5]
        if g["axis"] == "z":
            cands = [h for h in cands if h["axis"] == "Z"]
        else:
            cands = [h for h in cands if h["axis"] in ("X", "Y")]
        hole_err.append(min((h["diameter"] - g["d_mm"] for h in cands), key=abs) if cands else None)
    out["holes"] = hole_err
    th = [r["distance"] for r in m.get("plane_relations", []) if r["kind"] == "thickness"]
    out["wall6"] = min((t - 6.0 for t in th), key=abs) if th else None
    out["base8"] = min((t - 8.0 for t in th), key=abs) if th else None
    rec = trimesh.load(res.files["stl"])
    gt_local = tr["part_local_vertices"]
    out["surface"] = surface_deviation(rec, gt_local, tr["part_faces"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("synth")
    ap.add_argument("out")
    ap.add_argument("--mode", default="oracle", choices=["oracle", "oracle-pess", "lidar"])
    ap.add_argument("--keyframes", type=int, default=60)
    ap.add_argument("--candidates", type=int, default=200)
    ap.add_argument("--no-feature-ba", action="store_true")
    ap.add_argument("--no-board", action="store_true", help="tell the pipeline not to look for the board")
    ap.add_argument("--voxel", type=float, default=0.0)
    ap.add_argument("--set", nargs="*", default=[], help="extra cfg overrides key=value")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    from oracles import OracleDepth, OracleSegmenter
    cfg = make_config("Accurate (recommended)", n_keyframes=a.keyframes, max_candidates=a.candidates,
                      feature_ba=not a.no_feature_ba, use_board=not a.no_board, voxel_mm=a.voxel, device="cpu",
                      sift_features=4000)
    for kv in a.set:
        k, v = kv.split("=")
        cur = getattr(cfg, k)
        setattr(cfg, k, type(cur)(v) if not isinstance(cur, bool) else v.lower() in ("1", "true", "yes"))
    truth = os.path.join(a.synth, "truth.npz")
    seg = None if cfg.segmenter == "geometry" else OracleSegmenter(truth)
    if a.mode == "lidar":
        cfg.depth_model = "lidar"
        dm = None
    else:
        dm = OracleDepth(truth, pessimistic=(a.mode == "oracle-pess"))
    t0 = time.time()
    last = [0]

    def prog(p, msg):
        if time.time() - last[0] > 5:
            logging.info("%3d%% %s", int(p * 100), msg)
            last[0] = time.time()

    trd = np.load(truth)

    def pose_hook(kf, st):
        if st.world != "board":
            return
        from partscan.geometry import rot_angle_deg
        e = np.array([np.linalg.norm(st.T_wc[i][:3, 3] - trd["T_wc_part"][f.idx][:3, 3]) * 1000 for i, f in enumerate(kf)])
        r = np.array([rot_angle_deg(st.T_wc[i][:3, :3], trd["T_wc_part"][f.idx][:3, :3]) for i, f in enumerate(kf)])
        logging.info("POSE ERROR vs truth: median %.3f mm, p90 %.3f, max %.3f | rot median %.4f deg", np.median(e),
                     np.percentile(e, 90), e.max(), np.median(r))

    res = pipeline.run(os.path.join(a.synth, "stray_scan.zip"), a.out, cfg, progress=prog, depth_model=dm,
                       segmenter=seg, hooks={"poses": pose_hook})
    import gc
    gc.collect()
    ev = evaluate(res, a.synth)
    print("\n=== quality ===")
    for k, v in res.quality.items():
        print(f"  {k}: {v}")
    print("=== errors vs ground truth (mm) ===")
    print(json.dumps(ev, indent=1, default=lambda x: round(x, 4) if isinstance(x, float) else x))
    print("measured:", {k: round(v, 3) for k, v in res.measurements["overall"].items() if isinstance(v, float)})
    print("holes:", [(h["kind"], h["axis"], round(h["diameter"], 3), "rough" if h.get("rough") else "clean") for h in res.measurements.get("circles", [])])
    print("planes:", [(r["kind"], round(r["distance"], 3)) for r in res.measurements.get("plane_relations", [])])
    print(f"total {time.time() - t0:.0f}s")
    json.dump(dict(errors=ev, quality=res.quality), open(os.path.join(a.out, "eval.json"), "w"), indent=1,
              default=lambda x: x if not isinstance(x, np.generic) else x.item())


if __name__ == "__main__":
    main()
