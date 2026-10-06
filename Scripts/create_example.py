"""Recreate the included synthetic example using the production exporter."""
from pathlib import Path
import ctypes as C
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Tests'))
from test_core import build_core, arrays, Point, Line

def main():
    destination=ROOT/'Examples'/'box_export.zip'
    destination.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as work:
        core=build_core(work); vertices,triangles=arrays()
        lines=(Line*1)(Line(Point(0,0,0),Point(.2,.05,.1),b'Corner diagonal'))
        error=C.create_string_buffer(512)
        ok=core.sc_export_bundle(work.encode(),vertices,len(vertices),triangles,len(triangles),
                                lines,len(lines),1,b'DEMO',b'Synthetic 20 x 10 x 5 cm box',error,len(error))
        if not ok: raise RuntimeError(error.value.decode())
        if not core.sc_zip_bundle(work.encode(),str(destination).encode(),error,len(error)):
            raise RuntimeError(error.value.decode())
    print(f'Created {destination.name}: synthetic dimensions, 0.07 m2 surface, 0.001 m3 volume.')

if __name__=='__main__': main()
