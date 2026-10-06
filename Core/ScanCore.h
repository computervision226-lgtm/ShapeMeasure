#ifndef SHAPEMEASURE_SCANCORE_H
#define SHAPEMEASURE_SCANCORE_H
#include <stdint.h>
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif

typedef struct { double x, y, z; } SCPoint3; /* Input: right-handed, Y up. */
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
#ifdef __cplusplus
}
#endif
#endif
