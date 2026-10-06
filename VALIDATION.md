# Validation

## Executed in this workspace

- Production `ScanCore.c` compiled with C99, `-Wall -Wextra -Wpedantic -Werror`.
- 17 production-core automated tests passed. They execute the same C functions called by the iPhone app and independently parse the resulting SHP/SHX, DBF, ZIP, CSV, JSON and OBJ bytes.
- Known 20 × 10 × 5 cm box: expected width, depth, height, surface area, footprint, perimeter and volume.
- Reference scaling: length, area and volume follow scale, scale² and scale³.
- Translation: object relocation does not change measurements, and GIS output is normalized to a local origin.
- Incomplete, duplicate, inconsistent-winding and disconnected meshes do not receive an unsupported volume.
- Nonfinite coordinates, invalid triangle indices, degeneracy, unused vertices and invalid scales are rejected.
- Unverified scale cannot produce a metric export.
- MultiPatch triangle fans preserve vertical faces; convex footprint is closed and clockwise.
- Saved distances match exported endpoint coordinates, JSON, CSV and DBF records.
- Stored ZIP archive contains all 19 components and passes CRC/content checks; failure removes partial ZIP files.
- JSON preserves UTF-8 scan names and escapes quotes, backslashes and control characters.
- Example export is created by the production core, not a manually authored GIS mock.

Project metadata and asset catalogues are checked for presence and syntax when packaging. This does not replace Xcode compilation.

## Direct LiDAR point cloud checks

The new capture mode uses `ARFrame.sceneDepth`, camera intrinsics scaled to depth resolution, and the frame's camera transform. It samples measured depth/confidence pixels and voxel-filters the cloud. It does not use ARKit visual feature points, photo reconstruction, or a generated mesh as a substitute for LiDAR points.

The production C functions called by that screen passed 11 additional tests (28 total): camera-plane depth versus radial distance, image-to-camera axis signs, scaled intrinsics, a rotated/translated column-major pose, agreement from two camera positions observing the same static point, invalid depth/intrinsics/pose, BT.601/BT.709 and full/video range camera colour, independently decoded binary PLY schema/payload, metric XYZ coordinate preservation, rejected invalid clouds without overwriting a file, and failed destinations. The C compiler treats warnings as errors.

The project includes all three new Swift files. Its existing build configurations and shared scheme were verified unchanged after adding source references. `codemagic.yaml` adds **ShapeMeasure LiDAR build check** using the latest stable Xcode selection, unsigned compilation, and downloadable build logs; it requires no signing identities and does not publish a build. The signed TestFlight workflow retains its existing Apple integration, bundle identifier, distribution type, and Xcode selection.

**Pending on a cloud Mac and physical LiDAR iPhone:** compile the complete Swift app; verify camera permission denied/allowed flows; capture a static scene in portrait; confirm colours and axis orientation against known features; inspect the cloud from several camera positions; stop/resume without a tracking reset; interrupt/background/relocalize and verify accumulation remains blocked until Reset; export/reopen/share files; inspect the PLY in the chosen point cloud tool; exercise the point cap and monitor memory/temperature during capture; and compare independent measured distances. Swift syntax checks are not a substitute for framework type checking or a hardware run.

The TestFlight preparation adds a Mac archive helper, upload/install instructions, and the `ITSAppUsesNonExemptEncryption = NO` declaration for this source's lack of custom cryptography or a network backend. Shell syntax, argument validation, and the helper's unsupported-platform exit are checked in this workspace. Its real signed archive path still requires execution on a Mac with Xcode and your developer team.

The Codemagic preparation adds `codemagic.yaml`, `CODEMAGIC.md`, and a bundle-ID/build-number configuration helper. The workflow YAML and embedded shell syntax are checked locally. The helper is checked against copies of the real project for both target configurations, build counter/offset handling, idempotence, invalid inputs, and failure without partial writes. Cloud signing, iOS compilation, IPA export and upload remain unexecuted pending Codemagic and Apple credentials.

## Still requires Mac / iPhone validation

1. Run `bash Scripts/check_ios.sh` with an installed iOS SDK. Check Swift compilation and framework linking.
2. Run on the selected LiDAR iPhone and verify the guided capture state transitions, three-height passes and image saving.
3. Reconstruct a real rigid textured object; verify that the USDZ loads, axes and unit conversion are correct, and geometry matches the selected object.
4. Confirm import and camera-recording flows with Photos permissions, denied Camera access, portrait video orientation and representative image/video codecs.
5. Verify real 3D point picking, calibration, saved distances, reopened scans and share-sheet export.
6. Cancel during image import, video extraction and reconstruction. Start another capture and confirm the cancelled task cannot overwrite or remove it.
7. Scan several independently measured objects three times each. Compare a calibration dimension and additional uncalibrated dimensions, then record measurement error and repeatability. No accuracy threshold is claimed until this is completed.
8. Open each layer in the intended QGIS/ArcGIS installation; verify the engineering CRS and 3D MultiPatch display. No installed GIS application was available for an application-level compatibility test here.
9. Create a signed Release archive, validate and upload it through Xcode Organizer, wait for App Store Connect processing, add it to an internal TestFlight group, and verify installation on the intended iPhone.

No physical iPhone, iOS simulator, Xcode or Swift compiler is present in this Linux workspace. Therefore there is no executed iOS build, real capture test, or measured accuracy result in this delivery.
