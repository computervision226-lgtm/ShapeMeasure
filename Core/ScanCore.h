#ifndef SHAPEMEASURE_SCANCORE_H
#define SHAPEMEASURE_SCANCORE_H
#include <stdint.h>
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif

typedef struct { double x, y, z; } SCPoint3; /* Input: right-handed, Y up. */
typedef struct {
    float x, y, z; /* ARKit world coordinates, metres, Y up. */
    uint8_t red, green, blue, confidence; /* ARConfidenceLevel: 0, 1, 2. */
} SCCloudPoint;
typedef struct { int32_t a, b, c; } SCTriangle;
typedef struct { SCPoint3 a, b; char label[48]; } SCLine;
typedef struct {
    double width_m, depth_m, height_m;
    double surface_m2, footprint_m2, footprint_perimeter_m;
    double volume_m3; /* NAN unless one closed, consistently oriented component. */
    double min_x, min_y, min_z, max_x, max_y, max_z; /* Raw model coordinates. */
    int32_t boundary_edges, nonmanifold_edges, winding_errors, components;
    int32_t watertight, volume_valid, vertex_count, triangle_count;
} SCMetrics;

/* All functions return 1 on success, 0 on failure, and fill error when available.
   scale must be finite and > 0. Vertices must be welded and referenced by faces.
   Metrics describe reconstructed geometry, not a claimed instrument accuracy. */
int sc_analyze(const SCPoint3 *vertices, int32_t vertex_count,
               const SCTriangle *triangles, int32_t triangle_count,
               double scale, SCMetrics *metrics, char *error, size_t error_capacity);
int sc_export_bundle(const char *existing_directory,
                     const SCPoint3 *vertices, int32_t vertex_count,
                     const SCTriangle *triangles, int32_t triangle_count,
                     const SCLine *lines, int32_t line_count,
                     double scale, const char *scale_source, const char *object_name,
                     char *error, size_t error_capacity);
/* Stored ZIP, all generated components, portable and dependency-free. */
int sc_zip_bundle(const char *directory, const char *zip_path,
                  char *error, size_t error_capacity);
const char *sc_local_crs(void);
/* Depth is distance from the camera plane, not radial distance. Intrinsics
   must be scaled to the depth map. transform is ARCamera.transform, 16 floats
   in column-major order. Pixel coordinates use the native camera image,
   independent of screen orientation. Camera forward is negative Z. */
int sc_depth_unproject(float u, float v, float depth,
                       float fx, float fy, float cx, float cy,
                       const float *transform, SCPoint3 *world);
/* NV12 camera colour. bt709=0 uses BT.601, 1 uses BT.709. */
void sc_camera_rgb(uint8_t y, uint8_t cb, uint8_t cr, int full_range,
                   int bt709, uint8_t *rgb);
/* Binary little-endian PLY: float XYZ, uchar RGB, uchar confidence. No faces.
   XYZ is plain text, three columns in the same metric Y-up world frame.
   At most 500,000 points. Validation precedes opening the file; partial writes
   are removed on failure. Neither exporter changes the coordinates. */
int sc_write_cloud_ply(const char *path, const SCCloudPoint *points,
                       int32_t count, char *error, size_t error_capacity);
int sc_write_cloud_xyz(const char *path, const SCCloudPoint *points,
                       int32_t count, char *error, size_t error_capacity);
#ifdef __cplusplus
}
#endif
#endif
