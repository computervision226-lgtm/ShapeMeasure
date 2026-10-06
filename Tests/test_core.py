"""Execute the production C exporter; independently parse every exported format."""
import csv
import ctypes as C
import io
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]

class Point(C.Structure):
    _fields_ = [(n, C.c_double) for n in ('x', 'y', 'z')]

class Triangle(C.Structure):
    _fields_ = [(n, C.c_int32) for n in ('a', 'b', 'c')]

class Line(C.Structure):
    _fields_ = [('a', Point), ('b', Point), ('label', C.c_char * 48)]

class Metrics(C.Structure):
    _fields_ = [(n, C.c_double) for n in (
        'width_m', 'depth_m', 'height_m', 'surface_m2', 'footprint_m2',
        'footprint_perimeter_m', 'volume_m3', 'min_x', 'min_y', 'min_z', 'max_x', 'max_y', 'max_z')]
    _fields_ += [(n, C.c_int32) for n in (
        'boundary_edges', 'nonmanifold_edges', 'winding_errors', 'components',
        'watertight', 'volume_valid', 'vertex_count', 'triangle_count')]

VERTICES = [(0,0,0),(.2,0,0),(.2,0,.1),(0,0,.1),
            (0,.05,0),(.2,.05,0),(.2,.05,.1),(0,.05,.1)]
FACES = [(0,1,2),(0,2,3),(4,6,5),(4,7,6),(0,4,5),(0,5,1),
         (1,5,6),(1,6,2),(2,6,7),(2,7,3),(3,7,4),(3,4,0)]

def build_core(directory, sanitizer=False):
    lib = Path(directory) / ('libScanCore.dylib' if os.uname().sysname == 'Darwin' else 'libScanCore.so')
    command = [os.environ.get('CC', 'cc'), '-std=c99', '-Wall', '-Wextra', '-Wpedantic', '-Werror',
               '-shared', '-fPIC', '-O2', str(ROOT / 'Core' / 'ScanCore.c'), '-lm', '-o', str(lib)]
    subprocess.run(command, check=True)
    loaded = C.CDLL(str(lib))
    loaded.sc_analyze.argtypes = [C.POINTER(Point), C.c_int32, C.POINTER(Triangle), C.c_int32,
                                 C.c_double, C.POINTER(Metrics), C.c_char_p, C.c_size_t]
    loaded.sc_export_bundle.argtypes = [C.c_char_p, C.POINTER(Point), C.c_int32,
                                       C.POINTER(Triangle), C.c_int32, C.POINTER(Line), C.c_int32,
                                       C.c_double, C.c_char_p, C.c_char_p, C.c_char_p, C.c_size_t]
    loaded.sc_zip_bundle.argtypes = [C.c_char_p, C.c_char_p, C.c_char_p, C.c_size_t]
    return loaded

def arrays(vertices=VERTICES, faces=FACES):
    return (Point * len(vertices))(*(Point(*p) for p in vertices)), \
           (Triangle * len(faces))(*(Triangle(*f) for f in faces))

def parse_dbf(data):
    count, header, size = struct.unpack_from('<IHH', data, 4)
    fields = []
    for offset in range(32, header - 1, 32):
        descriptor = data[offset:offset + 32]
        fields.append((descriptor[:11].split(b'\0')[0].decode(), chr(descriptor[11]), descriptor[16]))
    rows = []
    for i in range(count):
        record = data[header + i*size:header + (i+1)*size]
        assert len(record) == size and record[0] == 32
        row = {}; offset = 1
        for name, kind, width in fields:
            value = record[offset:offset+width].decode().strip(); offset += width
            row[name] = (float(value) if value else None) if kind == 'N' else (value == 'T' if kind == 'L' else value)
        rows.append(row)
    assert data[header + count*size:] == b'\x1a'
    return rows

def parse_shapes(shp, shx):
    assert struct.unpack_from('>I', shp, 0)[0] == 9994
    assert struct.unpack_from('>I', shp, 24)[0] * 2 == len(shp)
    assert struct.unpack_from('<I', shp, 28)[0] == 1000
    kind = struct.unpack_from('<I', shp, 32)[0]
    assert struct.unpack_from('<I', shx, 32)[0] == kind
    assert struct.unpack_from('>I', shx, 24)[0] * 2 == len(shx)
    records = []; offset = 100
    while offset < len(shp):
        number, words = struct.unpack_from('>II', shp, offset)
        assert number == len(records) + 1
        index_offset, index_size = struct.unpack_from('>II', shx, 100 + (number-1)*8)
        assert index_offset*2 == offset and index_size == words
        rec = shp[offset+8:offset+8+words*2]
        assert len(rec) == words*2 and struct.unpack_from('<I', rec)[0] == kind
        nr, np = struct.unpack_from('<II', rec, 36)
        parts = list(struct.unpack_from(f'<{nr}I', rec, 44))
        at = 44 + nr*4
        types = list(struct.unpack_from(f'<{nr}I', rec, at)) if kind == 31 else None
        if kind == 31: at += nr*4
        xy = list(struct.unpack_from(f'<{np*2}d', rec, at)); at += np*16
        points = list(zip(xy[::2], xy[1::2]))
        zs = [0.0] * np
        if kind in (13,15,31):
            low, high = struct.unpack_from('<dd', rec, at); at += 16
            zs = list(struct.unpack_from(f'<{np}d', rec, at)); at += np*8
            assert abs(min(zs)-low) < 1e-12 and abs(max(zs)-high) < 1e-12
            measures = struct.unpack_from(f'<{np+2}d', rec, at); at += (np+2)*8
            assert all(m < -1e38 for m in measures)
        assert at == len(rec)
        records.append({'kind': kind, 'parts': parts, 'types': types,
                        'points': [(*p,z) for p,z in zip(points,zs)]})
        offset += 8 + words*2
    assert len(shx) == 100 + 8*len(records)
    return records

class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(); cls.core = build_core(cls.build.name)
    @classmethod
    def tearDownClass(cls): cls.build.cleanup()
    def setUp(self): self.work = tempfile.TemporaryDirectory(); self.addCleanup(self.work.cleanup)

    def analyze(self, vertices=VERTICES, faces=FACES, scale=1, expect=True):
        v, t = arrays(vertices, faces); m = Metrics(); err = C.create_string_buffer(512)
        ok = self.core.sc_analyze(v, len(v), t, len(t), scale, C.byref(m), err, len(err))
        self.assertEqual(bool(ok), expect, err.value.decode())
        return m, err.value.decode()

    def export(self, vertices=VERTICES, faces=FACES, scale=1, source=b'DEMO', name=b'Demo box', lines=(), expect=True):
        v, t = arrays(vertices, faces); ls = (Line * len(lines))(*lines); err = C.create_string_buffer(512)
        ok = self.core.sc_export_bundle(self.work.name.encode(), v, len(v), t, len(t), ls,
                                       len(ls), scale, source, name, err, len(err))
        self.assertEqual(bool(ok), expect, err.value.decode())
        return Path(self.work.name)

    def test_known_box_dimensions_area_volume(self):
        m,_ = self.analyze()
        for actual, expected in [(m.width_m,.2),(m.depth_m,.1),(m.height_m,.05),
                                 (m.surface_m2,.07),(m.footprint_m2,.02),(m.volume_m3,.001),
                                 (m.footprint_perimeter_m,.6)]: self.assertAlmostEqual(actual,expected,12)
        self.assertEqual((m.watertight,m.volume_valid,m.components), (1,1,1))

    def test_reference_scale_changes_length_area_volume(self):
        m,_ = self.analyze(scale=2.5)
        self.assertAlmostEqual(m.width_m,.5); self.assertAlmostEqual(m.surface_m2,.07*2.5**2)
        self.assertAlmostEqual(m.volume_m3,.001*2.5**3)

    def test_translation_does_not_change_measurements(self):
        shifted = [(x+1000,y-700,z+400) for x,y,z in VERTICES]
        m,_ = self.analyze(shifted)
        self.assertAlmostEqual(m.volume_m3,.001,9)
        output = self.export(vertices=shifted)
        mesh = parse_shapes((output/'mesh.shp').read_bytes(),(output/'mesh.shx').read_bytes())[0]
        self.assertAlmostEqual(min(p[0] for p in mesh['points']),-.1,9)
        self.assertAlmostEqual(max(p[2] for p in mesh['points']),.05,9)

    def test_open_bottom_has_no_volume(self):
        m,_ = self.analyze(faces=FACES[2:])
        self.assertFalse(m.volume_valid); self.assertEqual(m.boundary_edges,4)
        self.assertTrue(math.isnan(m.volume_m3))
        output = self.export(faces=FACES[2:])
        self.assertIsNone(json.loads((output/'report.json').read_text())['volume_m3'])
        self.assertIsNone(parse_dbf((output/'mesh.dbf').read_bytes())[0]['VOLUME_M3'])

    def test_inconsistent_winding_has_no_volume(self):
        faces = [(0,2,1),*FACES[1:]]
        m,_ = self.analyze(faces=faces)
        self.assertEqual(m.winding_errors,3); self.assertFalse(m.volume_valid)

    def test_duplicate_faces_have_no_volume(self):
        m,_ = self.analyze(faces=FACES+[FACES[0]])
        self.assertEqual(m.nonmanifold_edges,3); self.assertFalse(m.volume_valid)

    def test_multiple_components_have_no_volume(self):
        v = VERTICES+[(x+1,y,z) for x,y,z in VERTICES]
        faces = FACES+[tuple(i+8 for i in f) for f in FACES]
        m,_ = self.analyze(v,faces)
        self.assertEqual(m.components,2); self.assertTrue(m.watertight); self.assertFalse(m.volume_valid)

    def test_invalid_scales_rejected(self):
        for scale in (0,-1,float('nan'),float('inf')):
            with self.subTest(scale=scale): self.analyze(scale=scale,expect=False)

    def test_invalid_coordinates_indices_degeneracy_rejected(self):
        self.analyze(vertices=[(float('nan'),0,0),*VERTICES[1:]],expect=False)
        self.analyze(faces=[(0,1,80),*FACES[1:]],expect=False)
        self.analyze(faces=[(0,0,2),*FACES[1:]],expect=False)
        self.analyze(vertices=VERTICES+[(99,99,99)],expect=False)

    def test_unverified_metric_export_rejected(self): self.export(source=b'UNVERIFIED',expect=False)

    def test_multipatch_preserves_vertical_triangles(self):
        output = self.export()
        rec = parse_shapes((output/'mesh.shp').read_bytes(),(output/'mesh.shx').read_bytes())[0]
        self.assertEqual(rec['kind'],31); self.assertEqual(rec['parts'],list(range(0,36,3)))
        self.assertEqual(rec['types'],[1]*12); self.assertEqual(len(rec['points']),36)
        self.assertAlmostEqual(max(p[2] for p in rec['points']),.05)
        row = parse_dbf((output/'mesh.dbf').read_bytes())[0]
        self.assertAlmostEqual(row['AREA_M2'],.07); self.assertTrue(row['VOL_VALID'])

    def test_footprint_closed_clockwise(self):
        output = self.export()
        rec = parse_shapes((output/'footprint.shp').read_bytes(),(output/'footprint.shx').read_bytes())[0]
        p=rec['points']; self.assertEqual(p[0],p[-1]); self.assertEqual(len(p),5)
        area=sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(p,p[1:]))/2
        self.assertAlmostEqual(area,-.02)
        self.assertAlmostEqual(parse_dbf((output/'footprint.dbf').read_bytes())[0]['AREA_M2'],.02)

    def test_measurement_export_csv_dbf_and_coordinates_agree(self):
        line = Line(Point(*VERTICES[0]),Point(*VERTICES[6]),b'Corner, "diagonal"')
        output = self.export(scale=2,source=b'REFERENCE',lines=[line])
        recs = parse_shapes((output/'measures.shp').read_bytes(),(output/'measures.shx').read_bytes())
        rows = parse_dbf((output/'measures.dbf').read_bytes())
        report = json.loads((output/'report.json').read_text())
        csv_rows = list(csv.DictReader(io.StringIO((output/'measurements.csv').read_text())))
        self.assertEqual(len(recs),4); self.assertEqual(csv_rows[-1]['label'],'Corner, "diagonal"')
        for shape,row,item,csvrow in zip(recs,rows,report['measurements'],csv_rows):
            a,b = shape['points']; distance = math.dist(a,b)
            self.assertAlmostEqual(row['LENGTH_M'],distance,7)
            self.assertAlmostEqual(item['length_m'],distance,12)
            self.assertAlmostEqual(float(csvrow['length_m']),distance,11)

    def test_unicode_and_quotes_in_report(self):
        name = 'Test "box"\\line\nقياس'.encode()
        output = self.export(name=name)
        self.assertEqual(json.loads((output/'report.json').read_text())['object_name'],name.decode())

    def test_zip_every_component_crc_and_content(self):
        output = self.export(); archive = output/'scan.zip'; error = C.create_string_buffer(512)
        self.assertEqual(self.core.sc_zip_bundle(str(output).encode(),str(archive).encode(),error,len(error)),1)
        with zipfile.ZipFile(archive) as z:
            self.assertIsNone(z.testzip()); self.assertEqual(len(z.namelist()),19)
            for name in z.namelist(): self.assertEqual(z.read(name),(output/name).read_bytes())

    def test_missing_zip_component_removes_partial_archive(self):
        output = self.export(); (output/'model.obj').unlink(); archive=output/'incomplete.zip'; error=C.create_string_buffer(512)
        self.assertEqual(self.core.sc_zip_bundle(str(output).encode(),str(archive).encode(),error,len(error)),0)
        self.assertFalse(archive.exists())

    def test_obj_and_gis_share_metric_coordinates(self):
        output = self.export(scale=1.5)
        vertices = [tuple(map(float,line.split()[1:])) for line in (output/'model.obj').read_text().splitlines() if line.startswith('v ')]
        self.assertAlmostEqual(max(p[0] for p in vertices)-min(p[0] for p in vertices),.3)
        self.assertAlmostEqual(max(p[1] for p in vertices)-min(p[1] for p in vertices),.15)
        self.assertAlmostEqual(max(p[2] for p in vertices),.075)
        self.assertIn('LOCAL_CS', (output/'mesh.prj').read_text())
        self.assertNotIn('EPSG', (output/'mesh.prj').read_text())

if __name__ == '__main__': unittest.main(verbosity=2)
