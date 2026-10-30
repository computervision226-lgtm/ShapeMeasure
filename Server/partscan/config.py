"""All tunable settings in one place, with sensible presets."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class BoardSpec:
    """Printed ChArUco board. ``square_mm`` should be the size you MEASURED on the print."""
    squares_x: int = 8
    squares_y: int = 6
    square_mm: float = 30.0
    marker_mm: float = 22.0
    dictionary: str = "DICT_4X4_50"

    @property
    def marker_ratio(self) -> float:
        return self.marker_mm / self.square_mm


@dataclass
class Config:
    # ---- input ------------------------------------------------------------------------
    max_candidates: int = 360          # frames kept after sharpness screening (memory bound)
    n_keyframes: int = 90              # frames used for poses, depth and fusion
    min_sharpness_ratio: float = 0.35  # drop frames blurrier than this x median sharpness

    # ---- scale / poses ----------------------------------------------------------------
    use_board: bool = True             # auto-detected; disables itself if no board is seen
    board: BoardSpec = field(default_factory=BoardSpec)
    min_board_corners: int = 10
    refine_intrinsics: bool = True     # estimate radial distortion + focal correction from the board
    feature_ba: bool = True            # SIFT tracks + bundle adjustment (photogrammetric refinement)
    sift_features: int = 6000
    match_neighbors: int = 10          # pairs per keyframe for matching
    ba_max_points: int = 20000

    # ---- depth ------------------------------------------------------------------------
    depth_model: str = "promptda"      # "promptda" (AI, LiDAR-guided) | "lidar" (plain upsampling)
    promptda_checkpoint: str = "depth-anything/prompt-depth-anything-vitl-hf"
    promptda_size: int = 1008          # processing short side: 756 (fast), 1008, 1428 (sharpest)
    align_depth: bool = True           # per-frame scale/offset correction from triangulated points

    # ---- segmentation -----------------------------------------------------------------
    segmenter: str = "sam2"            # "sam2" | "geometry" (no AI; projected coarse shape)
    sam2_checkpoint: str = "facebook/sam2.1-hiera-large"
    carve_tolerance_px: float = 1.0    # how far outside a mask a voxel must project to be carved
    carve_min_views: int = 3           # robust visual hull: k-th strongest vote

    # ---- fusion / mesh ----------------------------------------------------------------
    voxel_mm: float = 0.0              # 0 = automatic (~max_dim/320, clamped 0.2..1.0 mm)
    trunc_voxels: float = 5.0          # TSDF truncation band in voxels
    trunc_back_ratio: float = 1.0      # band behind a surface relative to the front band. <1 helps very thin
                                       # walls but biases noisy depth outwards (erodes surfaces) - keep 1.0
    fusion_min_views: int = 2          # frames needed before a voxel counts as observed
    smooth_iterations: int = 8         # Taubin smoothing (volume preserving)
    max_faces: int = 400_000
    close_bottom: bool = True          # cap the unseen underside on the support plane

    # ---- measurement ------------------------------------------------------------------
    material: str = "Steel"
    reference_name: str = ""           # optional caliper check, e.g. "Length (X)"
    reference_mm: float = 0.0          # value you measured; 0 = no check
    apply_reference_scale: bool = False  # rescale the model so the reference matches exactly

    # ---- runtime ----------------------------------------------------------------------
    device: str = "auto"
    seed: int = 0
    debug: bool = False

    def to_dict(self):
        return asdict(self)


PRESETS = {
    "Fast (fewer frames)": dict(n_keyframes=45, promptda_size=756, sift_features=4000, max_candidates=240),
    "Accurate (recommended)": dict(),
    "Max detail (slow)": dict(n_keyframes=120, promptda_size=1428, sift_features=8000, voxel_mm=0.0,
                              max_candidates=480),
}


def make_config(preset: str = "Accurate (recommended)", **overrides) -> Config:
    cfg = Config()
    for k, v in {**PRESETS.get(preset, {}), **overrides}.items():
        if k == "board" and isinstance(v, dict):
            v = BoardSpec(**v)
        setattr(cfg, k, v)
    return cfg


MATERIAL_DENSITY_G_CM3 = {
    "Steel": 7.85, "Stainless steel": 8.00, "Aluminium 6061": 2.70, "Titanium Ti-6Al-4V": 4.43,
    "Brass": 8.50, "Copper": 8.96, "Cast iron": 7.20, "PLA": 1.24, "ABS": 1.04, "Nylon (PA12)": 1.01,
    "POM / Delrin": 1.41, "None": 0.0,
}


BOARDS = {
    "Standard 8x6 (Letter / A4)": dict(squares_x=8, squares_y=6, square_mm=30.0, marker_mm=22.0),
    "Large 11x7 (A3 / Tabloid)": dict(squares_x=11, squares_y=7, square_mm=35.0, marker_mm=26.0),
}
SCALE_CHECKS = {"printed 100 mm ruler": None, "6 squares between inner corners": 6}


def board_from_check(board_name: str, measured_mm: float, measured_on: str = "printed 100 mm ruler"):
    """BoardSpec scaled by what the user measured on the print. Returns (spec, note-or-empty)."""
    base = BOARDS.get(board_name, BOARDS["Standard 8x6 (Letter / A4)"])
    n_sq = SCALE_CHECKS.get(measured_on)
    nominal = 100.0 if n_sq is None else n_sq * base["square_mm"]
    note = ""
    if not measured_mm or measured_mm <= 0:
        ratio = 1.0
        note = "No print scale check entered - assuming the board printed at exactly 100 %."
    else:
        ratio = float(measured_mm) / nominal
        if abs(ratio - 1) > 0.03:
            note = (f"Your print check ({measured_mm:.2f} mm for {nominal:.1f} mm nominal) is {100 * (ratio - 1):+.1f} % off. "
                    "Printers are normally within 1 %; please re-check the measurement and that you printed at 100 %.")
    spec = BoardSpec(squares_x=base["squares_x"], squares_y=base["squares_y"],
                     square_mm=base["square_mm"] * ratio, marker_mm=base["marker_mm"] * ratio)
    return spec, note
