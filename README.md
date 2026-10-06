# ShapeMeasure for iPhone

A native iPhone source project for **small rigid objects → reconstructed 3D geometry → measurements → GIS shapefiles**.

This first version targets a supported **LiDAR iPhone Pro running iOS 17 or later**. It uses Apple's Object Capture and on-device photogrammetry. There is no backend, account system, API key, or cloud reconstruction bill. Runtime support checks determine whether capture and reconstruction are available on the device.

**Delivery status:** the production C geometry/export engine compiles and its automated tests pass in this workspace. The Xcode project and Swift interface are supplied as source. iOS compilation, camera capture, reconstruction, UI interaction, and real-world accuracy still need validation on a Mac and physical iPhone. This is a development prototype, not a signed app or an App Store release.

For **TestFlight through Codemagic**, follow **[CODEMAGIC.md](CODEMAGIC.md)** and use the included `codemagic.yaml`. Codemagic supplies the cloud Mac/Xcode build machine, so local Xcode installation is optional. The guide covers Apple signing, cloud upload, and installing through an internal testing group. No cloud build has run or been uploaded yet.

For the **local Mac route**, follow **[TESTFLIGHT.md](TESTFLIGHT.md)**. The package also includes `Scripts/archive_testflight.sh` for creating a signed archive on your Mac.

## Install on your iPhone

1. Unzip this package on a **Mac**. For TestFlight, Apple currently requires Xcode 26 or later and the iOS 26 SDK or later. Select a compatible non-beta release using [Apple's Mac/Xcode requirements](https://developer.apple.com/xcode/system-requirements). Direct device testing also needs an Xcode release that supports your phone's installed iOS version.
2. Open **`ShapeMeasure.xcodeproj`**. The project and shared scheme are already included. No dependency installation or project generator is required.
3. Select the blue project in Xcode, select the **ShapeMeasure** target, and open **Signing & Capabilities**.
4. Select your Apple development team and change `com.example.ShapeMeasure` to your own unique bundle identifier. Leave automatic signing enabled.
5. Connect and unlock your iPhone, trust the Mac if prompted, and select it as the run destination.
6. Enable **Settings → Privacy & Security → Developer Mode** on the phone if Xcode requests it. Complete the restart and on-device confirmation.
7. Press **Run** in Xcode. Allow camera access. The optional video recorder also requests microphone access; reconstruction does not use audio.
8. Tap **Try demo** first. The synthetic object should show **20 cm width, 10 cm depth, 5 cm height**, surface area **700 cm²**, footprint **200 cm²**, and closed-mesh volume **1,000 cm³**. Export the demo to inspect the GIS files.

These steps install a development build directly from Xcode. To install through TestFlight, complete the signing/upload/group setup in [TESTFLIGHT.md](TESTFLIGHT.md). Developer Mode applies to direct Xcode testing, not TestFlight installation.

## Scan a small object

1. Choose a rigid object with visible texture and diffuse lighting. Keep the object still.
2. Tap **Scan an object**, center it, tap **Select object**, and adjust the bounding box around the object.
3. Tap **Start capture** and slowly circle it. Apple's capture dial shows missing viewpoints. After completing one pass, add another pass at a different height. Aim for three heights.
4. Capture at least 20 images. A handful of unrelated views is insufficient for the intended measured reconstruction. More overlapping angles usually help; this version deliberately requires at least 20 images.
5. Tap **Build 3D shape**. Keep the app open while reconstruction runs on the phone.
6. Inspect the model for missing sides, support surfaces, or artifacts. Rotate by dragging and zoom by pinching.
7. Check scale against a ruler. Guided capture retains LiDAR/depth scale information, but it remains an estimate. Tap two object points whose distance you know, enter that real distance, and tap **Apply known length** to correct the scale. Alternatively, compare a dimension with a ruler and choose **Keep LiDAR estimate**.
8. Tap two visible surface points to measure a straight distance, give it a label, and tap **Save distance**. Rotate to reach other sides. Distances update when scale changes.
9. Tap **Export shapefiles** and choose Save to Files, AirDrop, or another share destination.

Very shiny, transparent, deformable, repetitive, very thin, or very small objects can reconstruct poorly. This version scans multiple heights without a flip workflow, so the underside may be missing. The app does not invent a volume when the resulting mesh is open.

## Import images or a video

- **Photos:** select 20–120 overlapping views of the same object. Use a stationary object and move the camera around it. Photos are orientation-corrected and converted to JPEG at up to 2,400 pixels on the long side.
- **Imported video:** select a 10–120 second video circling the object. The app extracts 20–100 frames across its duration.
- **Recorded video:** record a 10–90 second orbit with the built-in camera recorder.
- Use multiple heights and clear, sharp images. An orbit video with motion blur can be less useful than guided still captures.
- **All imported photos and video require a known-length calibration before metric export.** The import path does not preserve LiDAR depth metadata, so it never assumes that the reconstructed model already has verified physical scale.
- No foreground segmentation editor or frame quality scoring is included. Apple's reconstruction object masking is enabled; inspect and recapture if the model includes background geometry. The frame sampler distributes frames in time and cannot guarantee angular coverage.

## Files in the exported ZIP

| File set | Geometry / contents | Use |
|---|---|---|
| `mesh.shp`, `.shx`, `.dbf`, `.prj`, `.cpg` | 3D MultiPatch triangle fans, shape type 31 | The reconstructed 3D object, including vertical faces; a compatible 3D GIS viewer is required. |
| `footprint.shp`, `.shx`, `.dbf`, `.prj`, `.cpg` | 2D clockwise polygon, shape type 5 | Convex footprint for ordinary GIS viewing. It includes concave voids. |
| `measures.shp`, `.shx`, `.dbf`, `.prj`, `.cpg` | PolyLineZ, shape type 13 | Axis-aligned width/depth/height lines and saved point-to-point distances. |
| `model.obj` | Calibrated triangle geometry | A portable geometry model in the same local metric frame. Textures are not included in this OBJ. |
| `report.json` | Dimensions, areas, optional volume, coordinate transform, scale source, topology, line endpoints | Full measurement record and interpretation. |
| `measurements.csv` | Labelled lengths in metres | Spreadsheet import. |
| `READ_ME.txt` | Export interpretation | Explanation to keep alongside the GIS files. |

Keep every shapefile's five companion files together. **A `.shp` alone is not a complete dataset.**

### Open in GIS software

1. Unzip the exported ZIP into a folder.
2. Open `footprint.shp` in QGIS or ArcGIS first, then `measures.shp`.
3. For the complete 3D geometry, open `mesh.shp` in a compatible 3D scene viewer. MultiPatch support varies among GIS readers. An ArcGIS Pro local scene is a suitable target for device validation.
4. Inspect the attribute table for dimensions and scale source. Reports and OBJ provide another way to inspect the geometry if the GIS reader does not render MultiPatch.

**Coordinates are local engineering coordinates in metres, not GPS coordinates.** The origin is the object's horizontal bounding-box center at its lowest reconstructed point. Export axes are X horizontal, Y horizontal, Z up. No GPS positioning, geographic CRS, UTM conversion, or georeferencing is claimed. Do not assign EPSG:4326 to these coordinates. Add control points and a coordinate transform before placing the object in a geographic map.

## What the measurements mean

| Measurement | Calculation | Limitation |
|---|---|---|
| Width, depth, height | Extents of the reconstructed mesh along the model axes, multiplied by scale | Axis aligned; not the minimum oriented box. Outliers and rotation affect them. |
| Saved distance | Euclidean distance between two selected model points | Straight line, not a geodesic path over the surface. |
| Surface area | Sum of all triangle areas | Area of the reconstructed mesh; holes and artifacts affect it. |
| Footprint | Convex hull of XY-projected vertices | Includes concave empty regions; not the exact ground-contact outline. |
| Volume | Absolute signed triangle volume | Only emitted for a single closed, consistently oriented component. Self-intersection is not checked; validate the model visually. |

Scale sources in the report and attribute tables:

- `LIDAR`: guided capture, using the LiDAR-assisted scale estimate the user chose to retain.
- `REFERENCE`: the user calibrated the mesh using a known distance.
- `DEMO`: synthetic geometry, not a real capture.

There is **no claimed centimetre, millimetre, or survey accuracy**. Decimal places are storage/display precision. Test repeatability and compare independent known distances on real objects before using measurements for decisions. Very small objects often expose capture and point-selection limits; calibration establishes scale but does not remove reconstruction error.

## Develop and verify

The project has no third-party Swift dependencies. Its source is organized as follows:

```text
App/                  SwiftUI capture/import/reconstruction/review and persistence
Core/ScanCore.c        Production mesh analysis, shapefile/DBF/OBJ/JSON/CSV/ZIP export
Core/ScanCore.h        C/Swift interface
ShapeMeasure.xcodeproj Ready-to-open Xcode project and shared scheme
Tests/test_core.py     Production-core tests with independent binary parsers
Scripts/              Reproducible verification, project generation and example creation
Examples/box_export.zip Synthetic sample exported by the production core
VALIDATION.md          Executed checks and remaining physical-device checks
TESTFLIGHT.md          Mac signing, archive/upload, tester installation and troubleshooting
CODEMAGIC.md           Cloud signing/build/upload and TestFlight setup
codemagic.yaml         Native iOS cloud workflow using Xcode 26.6
```

Run the portable verification on Linux or macOS with a C compiler and Python 3:

```bash
bash Scripts/verify.sh
```

Run the unsigned iOS compile check on a Mac with Xcode:

```bash
bash Scripts/check_ios.sh
```

Recreate the sample export or regenerate the project after adding sources:

```bash
python3 Scripts/create_example.py
python3 Scripts/generate_project.py
```

Regenerating the project restores its default bundle identifier, empty team, version and build settings. Preserve your signing settings and reapply them after regeneration. No regeneration is needed for the included project.

The simulator can exercise the synthetic demo, persistence, model interaction and exporter. Actual capture and reconstruction require supported physical hardware. Device scans are kept in the app's Documents/Scans directory and can be accessed through Files or Xcode. Deleting a scan also removes its input images, checkpoints, reconstructed model and exports.

## Deliberate first-version scope

Included: guided capture, photo/video import, on-device reconstruction, geometry preview, ruler calibration, saved distances, scan persistence, metric shapefile/OBJ/JSON/CSV export and a synthetic demo.

Further work: flipped-object capture with pause/resume guidance, crop/cleanup tools, frame sharpness and viewpoint checks, export of calibrated textured USDZ, tighter topology/self-intersection validation, oriented dimensions, georeferencing/control points, object labels, and real-device accuracy benchmarking. Semantic labels and AI-generated geometry do not establish metric truth; measured geometry must retain a real scale source.

## Primary technical references

- [Apple Object Capture](https://developer.apple.com/documentation/realitykit/realitykit-object-capture)
- [Apple: Meet Object Capture for iOS](https://developer.apple.com/videos/play/wwdc2023/10191/)
- [Apple PhotogrammetrySession](https://developer.apple.com/documentation/realitykit/photogrammetrysession)
- [Apple: object masking configuration](https://developer.apple.com/documentation/realitykit/photogrammetrysession/configuration-swift.struct/isobjectmaskingenabled)
- [Apple SceneKit loading options](https://developer.apple.com/documentation/scenekit/scnscenesource/loadingoption)
- [Apple: run on physical devices](https://developer.apple.com/documentation/xcode/running-your-app-on-simulated-or-physical-devices)
- [Apple: enable Developer Mode](https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device)
- [Esri Shapefile Technical Description (PDF)](https://www.esri.com/library/whitepapers/pdfs/shapefile.pdf)

The source uses documented Apple APIs and the published Esri binary specification. It does not include Apple's sample-app source or a third-party reconstruction model.
