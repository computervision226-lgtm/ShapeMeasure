"""Test the production depth unprojection, camera colour, and point exporters.

These functions are called by the ARKit capture screen. No Apple framework or
simulated LiDAR is used here; the physical sensor/UI require an iPhone test.
"""
import ctypes as C
import math
from pathlib import Path
import struct
import tempfile
import unittest

from test_core import Point, build_core


class CloudPoint(C.Structure):
    _fields_ = [(name, C.c_float) for name in ('x', 'y', 'z')]
    _fields_ += [(name, C.c_uint8) for name in ('red', 'green', 'blue', 'confidence')]


IDENTITY = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)


class PointCloudTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.lib = build_core(cls.temp.name)
        cls.lib.sc_depth_unproject.argtypes = [C.c_float] * 7 + [C.POINTER(C.c_float), C.POINTER(Point)]
        cls.lib.sc_camera_rgb.argtypes = [C.c_uint8, C.c_uint8, C.c_uint8, C.c_int, C.c_int, C.POINTER(C.c_uint8)]
        for name in ('sc_write_cloud_ply', 'sc_write_cloud_xyz'):
            getattr(cls.lib, name).argtypes = [C.c_char_p, C.POINTER(CloudPoint), C.c_int32, C.c_char_p, C.c_size_t]

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def unproject(self, u, v, d, fx=200, fy=200, cx=128, cy=96, pose=IDENTITY):
        matrix = (C.c_float * 16)(*pose)
        point = Point(123, 456, 789)
        ok = self.lib.sc_depth_unproject(u, v, d, fx, fy, cx, cy, matrix, C.byref(point))
        return ok, (point.x, point.y, point.z)

    def test_principal_pixel_and_image_axes(self):
        self.assertEqual(self.unproject(128, 96, 2), (1, (0, 0, -2)))
        self.assertEqual(self.unproject(228, 196, 2), (1, (1, -1, -2)))
        # An off-axis depth is distance from the camera plane, not ray length.
        self.assertAlmostEqual(math.dist((0, 0, 0), self.unproject(228, 196, 2)[1]), math.sqrt(6))

    def test_scaled_image_intrinsics_preserve_ray(self):
        original = self.unproject(1600, 900, 1.5, 2000, 1800, 960, 540)
        resized = self.unproject(160, 90, 1.5, 200, 180, 96, 54)
        self.assertEqual(original, resized)

    def test_world_pose_rotation_and_translation(self):
        # 90 degrees about Y, then translation (10, 20, 30); column-major.
        pose = (0, 0, -1, 0, 0, 1, 0, 0, 1, 0, 0, 0, 10, 20, 30, 1)
        self.assertEqual(self.unproject(228, 196, 2, pose=pose), (1, (8, 19, 29)))

    def test_two_camera_positions_agree_on_static_plane(self):
        p1 = self.unproject(128, 96, 2)[1]
        pose = list(IDENTITY); pose[12] = 1
        p2 = self.unproject(28, 96, 2, pose=pose)[1]
        self.assertEqual(p1, p2)

    def test_invalid_depth_intrinsics_pose_rejected_without_output_change(self):
        for d in (0, -1, math.nan, math.inf):
            self.assertEqual(self.unproject(0, 0, d), (0, (123, 456, 789)))
        for bad in (0, -1, math.nan, math.inf):
            self.assertEqual(self.unproject(0, 0, 1, fx=bad)[0], 0)
            self.assertEqual(self.unproject(0, 0, 1, fy=bad)[0], 0)
        pose = list(IDENTITY); pose[12] = math.nan
        self.assertEqual(self.unproject(0, 0, 1, pose=pose)[0], 0)
        self.assertEqual(self.unproject(math.inf, 0, 1)[0], 0)

    def rgb(self, y, cb, cr, full=True, bt709=False):
        values = (C.c_uint8 * 3)()
        self.lib.sc_camera_rgb(y, cb, cr, int(full), int(bt709), values)
        return tuple(values)

    def test_camera_full_and_video_range_neutral_endpoints(self):
        self.assertEqual(self.rgb(0, 128, 128), (0, 0, 0))
        self.assertEqual(self.rgb(255, 128, 128), (255, 255, 255))
        self.assertEqual(self.rgb(16, 128, 128, full=False), (0, 0, 0))
        self.assertEqual(self.rgb(235, 128, 128, full=False), (255, 255, 255))
        self.assertEqual(self.rgb(128, 128, 128), (128, 128, 128))

    def test_camera_chroma_matrix_and_clipping(self):
        self.assertEqual(self.rgb(100, 128, 200), (201, 49, 100))
        self.assertEqual(self.rgb(100, 128, 200, bt709=True), (213, 66, 100))
        self.assertEqual(self.rgb(255, 255, 255)[0], 255)
        self.assertEqual(self.rgb(0, 0, 0)[0], 0)

    def export(self, writer, path, points):
        array = (CloudPoint * len(points))(*(CloudPoint(*p) for p in points))
        error = C.create_string_buffer(512)
        ok = writer(str(path).encode(), array, len(array), error, len(error))
        return ok, error.value

    def test_binary_ply_exact_schema_and_payload(self):
        source = [(0.25, -0.5, -2, 255, 0, 128, 2), (1, 2, 3, 1, 2, 3, 1)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cloud.ply'
            self.assertEqual(self.export(self.lib.sc_write_cloud_ply, path, source)[0], 1)
            header, payload = path.read_bytes().split(b'end_header\n', 1)
            lines = header.decode('ascii').splitlines()
            self.assertEqual(lines[:2], ['ply', 'format binary_little_endian 1.0'])
            self.assertIn('element vertex 2', lines)
            self.assertEqual([line for line in lines if line.startswith('property ')],
                ['property float x', 'property float y', 'property float z',
                 'property uchar red', 'property uchar green', 'property uchar blue', 'property uchar confidence'])
            self.assertFalse(any('face' in line for line in lines))
            self.assertEqual(len(payload), 16 * len(source))
            rows = list(struct.iter_unpack('<fffBBBB', payload))
            self.assertEqual(rows, source)
            self.assertIn('Y up', header.decode('ascii'))

    def test_xyz_uses_same_metric_coordinates_without_axis_swap(self):
        source = [(0.123456791, -1.25, 3.5, 1, 2, 3, 2), (-2, 0, 1, 5, 6, 7, 0)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cloud.xyz'
            self.assertEqual(self.export(self.lib.sc_write_cloud_xyz, path, source)[0], 1)
            rows = [tuple(map(float, row.split())) for row in path.read_text().splitlines()]
            self.assertEqual(len(rows), 2)
            for actual, expected in zip(rows, source):
                for a, e in zip(actual, expected[:3]):
                    self.assertAlmostEqual(a, C.c_float(e).value, places=8)

    def test_invalid_cloud_does_not_create_or_overwrite_output(self):
        invalid = [[], [(math.nan, 0, 0, 0, 0, 0, 2)], [(0, math.inf, 0, 0, 0, 0, 2)],
                   [(0, 0, 1001, 0, 0, 0, 2)], [(0, 0, 0, 0, 0, 0, 3)]]
        for writer in (self.lib.sc_write_cloud_ply, self.lib.sc_write_cloud_xyz):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'existing'
                path.write_bytes(b'prior export')
                for source in invalid:
                    ok, error = self.export(writer, path, source)
                    self.assertEqual(ok, 0)
                    self.assertTrue(error)
                    self.assertEqual(path.read_bytes(), b'prior export')
                self.assertEqual(writer(str(path).encode(), None, 500001, None, 0), 0)

    def test_unwritable_destination_reports_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing' / 'cloud'
            for writer in (self.lib.sc_write_cloud_ply, self.lib.sc_write_cloud_xyz):
                ok, error = self.export(writer, path, [(0, 0, -1, 20, 30, 40, 2)])
                self.assertEqual(ok, 0)
                self.assertTrue(error)
                self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
