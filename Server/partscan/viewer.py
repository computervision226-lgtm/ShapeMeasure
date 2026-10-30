"""Self-contained interactive 3D viewer (three.js inlined; works offline, phone friendly).

Tap/click two points on the part to measure the distance between them (mm, with dX/dY/dZ).
Buttons toggle the bounding-box dimensions, detected holes, colour and wireframe.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import tarfile
import urllib.request

import numpy as np

log = logging.getLogger("partscan")
THREE_VERSION = "0.170.0"
_FILES = {"three": "build/three.module.min.js", "orbit": "examples/jsm/controls/OrbitControls.js"}
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "partscan", f"three-{THREE_VERSION}")


def _fetch(url, timeout=20):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def three_sources() -> dict | None:
    """three.js module sources (cached). Tries jsDelivr, then the npm registry tarball."""
    os.makedirs(CACHE, exist_ok=True)
    paths = {k: os.path.join(CACHE, os.path.basename(v)) for k, v in _FILES.items()}
    if all(os.path.exists(p) for p in paths.values()):
        return {k: open(p, "rb").read() for k, p in paths.items()}
    try:
        for k, rel in _FILES.items():
            data = _fetch(f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/{rel}")
            open(paths[k], "wb").write(data)
        return {k: open(p, "rb").read() for k, p in paths.items()}
    except Exception as ex:
        log.info("jsDelivr unavailable (%s); trying the npm registry", ex)
    try:
        tgz = _fetch(f"https://registry.npmjs.org/three/-/three-{THREE_VERSION}.tgz", timeout=60)
        with tarfile.open(fileobj=io.BytesIO(tgz)) as tf:
            for k, rel in _FILES.items():
                open(paths[k], "wb").write(tf.extractfile("package/" + rel).read())
        return {k: open(p, "rb").read() for k, p in paths.items()}
    except Exception as ex:
        log.warning("could not download three.js (%s); the viewer will load it from the CDN when opened", ex)
        return None


def _b64(a: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode("ascii")


def viewer_payload(mesh_m, meas: dict, max_faces: int = 160_000) -> dict:
    m = mesh_m.copy()
    m.apply_scale(1000.0)
    src_V = np.asarray(m.vertices)
    src_C = (np.asarray(m.visual.vertex_colors[:, :3], np.uint8)
             if getattr(m.visual, "kind", None) == "vertex" and len(m.visual.vertex_colors) == len(src_V) else None)
    if len(m.faces) > max_faces:
        try:
            m = m.simplify_quadric_decimation(face_count=max_faces)
        except Exception:
            pass
    V = np.asarray(m.vertices, np.float32)
    F = np.asarray(m.faces, np.uint32)
    if src_C is not None:   # decimation drops vertex colours: take them from the nearest original vertex
        from scipy.spatial import cKDTree
        C = src_C[cKDTree(src_V).query(V)[1]]
    else:
        C = np.full((len(V), 3), 180, np.uint8)
    from .measure import best_dims
    o = meas["overall"]
    L, W, H, _ = best_dims(meas)
    circles = [dict(kind=c["kind"], axis=c["axis"], d=round(c["diameter"], 3), c=[round(v, 3) for v in c["center"]],
                    length=round(c["length"], 2), rough=bool(c.get("rough"))) for c in meas.get("circles", [])]
    return dict(V=_b64(V), F=_b64(F), C=_b64(C), nv=len(V), nf=len(F),
                bbox=dict(min=o["bbox_min"], max=o["bbox_max"], L=L, W=W, H=H),
                circles=circles)


CSS = """
:root{--bg:#f4f6f9;--panel:rgba(255,255,255,.92);--ink:#16202b;--muted:#5b6876;--accent:#c2410c;--blue:#1d4ed8;--line:#d6dde5}
@media (prefers-color-scheme:dark){:root{--bg:#11161c;--panel:rgba(25,31,39,.92);--ink:#e8edf2;--muted:#9aa7b4;--accent:#fb923c;--blue:#7aa2ff;--line:#2c3540}}
html,body{margin:0;height:100%;background:var(--bg);color:var(--ink);font:14px/1.35 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;overflow:hidden}
#app{position:fixed;inset:0;touch-action:none}
#panel{position:fixed;left:12px;top:12px;right:12px;max-width:430px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:10px 12px;backdrop-filter:blur(6px);box-shadow:0 4px 18px rgba(0,0,0,.08)}
#panel h1{font-size:14px;margin:0 0 6px;font-weight:650}
.row{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0}
button{font:inherit;font-size:13px;padding:6px 10px;border-radius:8px;border:1px solid var(--line);background:transparent;color:var(--ink);cursor:pointer}
button.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
#readout{font-variant-numeric:tabular-nums;max-height:30vh;overflow:auto}
#readout div{padding:3px 0;border-top:1px solid var(--line)}
.hint{color:var(--muted);font-size:12.5px}
.lbl{position:fixed;transform:translate(-50%,-50%);pointer-events:none;font:600 12px system-ui,sans-serif;padding:2px 6px;border-radius:6px;background:var(--panel);border:1px solid var(--line);white-space:nowrap;font-variant-numeric:tabular-nums}
.lbl.dim{color:var(--accent)} .lbl.hole{color:var(--blue)} .lbl.meas{color:var(--ink);background:#fde68a;color:#3b2a00;border-color:#f59e0b}
"""

JS = r"""
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
const D = JSON.parse(document.getElementById('partscan-data').textContent);
function dec(s, T){const b=atob(s);const u=new Uint8Array(b.length);for(let i=0;i<b.length;i++)u[i]=b.charCodeAt(i);return new T(u.buffer);}
const V=dec(D.V,Float32Array), F=dec(D.F,Uint32Array), C=dec(D.C,Uint8Array);
const app=document.getElementById('app');
const renderer=new THREE.WebGLRenderer({antialias:true,alpha:true,preserveDrawingBuffer:true});
renderer.setPixelRatio(Math.min(2,window.devicePixelRatio||1));
app.appendChild(renderer.domElement);
const scene=new THREE.Scene();
const cam=new THREE.PerspectiveCamera(35,1,0.1,100000); cam.up.set(0,0,1);
const ctl=new OrbitControls(cam,renderer.domElement); ctl.enableDamping=true;
scene.add(new THREE.HemisphereLight(0xffffff,0x667788,1.6));
const sun=new THREE.DirectionalLight(0xffffff,1.6); sun.position.set(1,-1.5,2.5); scene.add(sun);
const g=new THREE.BufferGeometry();
g.setAttribute('position',new THREE.BufferAttribute(V,3));
g.setAttribute('color',new THREE.BufferAttribute(C,3,true));
g.setIndex(new THREE.BufferAttribute(F,1)); g.computeVertexNormals(); g.computeBoundingSphere();
const matC=new THREE.MeshStandardMaterial({vertexColors:true,roughness:.75,metalness:.05});
const matG=new THREE.MeshStandardMaterial({color:0xb9c3cf,roughness:.55,metalness:.1});
const mesh=new THREE.Mesh(g,matC); scene.add(mesh);
const wire=new THREE.LineSegments(new THREE.WireframeGeometry(g),new THREE.LineBasicMaterial({color:0x334155,transparent:true,opacity:.25})); wire.visible=false; scene.add(wire);
const bb=D.bbox, mn=new THREE.Vector3(...bb.min), mx=new THREE.Vector3(...bb.max);
const ctr=mn.clone().add(mx).multiplyScalar(.5), size=mx.clone().sub(mn), R=size.length();
const grid=new THREE.GridHelper(Math.ceil(R*1.6/10)*10,Math.ceil(R*1.6/10),0x94a3b8,0xcbd5e1);
grid.rotation.x=Math.PI/2; grid.position.set(ctr.x,ctr.y,0); grid.material.transparent=true; grid.material.opacity=.45; scene.add(grid);
function resetView(){const fv=cam.fov*Math.PI/180, fh=2*Math.atan(Math.tan(fv/2)*cam.aspect);
  const dist=(R/2)/Math.sin(Math.min(fv,fh)/2)*1.12;
  ctl.target.copy(ctr);cam.position.copy(ctr).add(new THREE.Vector3(1.1,-1.5,1.0).normalize().multiplyScalar(dist));ctl.update();}
// ---- labels
const labels=[];
function label(text,pos,cls,group){const el=document.createElement('div');el.className='lbl '+cls;el.textContent=text;document.body.appendChild(el);const L={el,pos:pos.clone(),group};labels.push(L);return L;}
function lineMat(c){return new THREE.LineBasicMaterial({color:c,depthTest:false,transparent:true});}
// ---- bounding box dims
const boxG=new THREE.Group(); scene.add(boxG);
{const b=new THREE.Box3(mn,mx); const h=new THREE.Box3Helper(b,0xc2410c); h.material.depthTest=false; h.material.transparent=true; h.material.opacity=.6; boxG.add(h);
 label('L '+bb.L.toFixed(2),new THREE.Vector3(ctr.x,mn.y,mn.z),'dim','box');
 label('W '+bb.W.toFixed(2),new THREE.Vector3(mx.x,ctr.y,mn.z),'dim','box');
 label('H '+bb.H.toFixed(2),new THREE.Vector3(mx.x,mn.y,ctr.z),'dim','box');}
// ---- holes
const holeG=new THREE.Group(); scene.add(holeG);
for(const c of D.circles){
  const pts=[];const r=c.d/2;const ax=c.axis;
  for(let k=0;k<=64;k++){const t=k/64*Math.PI*2;const a=r*Math.cos(t),b=r*Math.sin(t);
    pts.push(ax==='Z'?new THREE.Vector3(c.c[0]+a,c.c[1]+b,c.c[2]):ax==='Y'?new THREE.Vector3(c.c[0]+a,c.c[1],c.c[2]+b):new THREE.Vector3(c.c[0],c.c[1]+a,c.c[2]+b));}
  const ln=new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),lineMat(0x1d4ed8)); ln.renderOrder=2; holeG.add(ln);
  label(c.rough?'≈Ø'+c.d.toFixed(1)+' (rough)':'Ø'+c.d.toFixed(2),new THREE.Vector3(...c.c),'hole','holes');
}
// ---- point-to-point measuring
const measG=new THREE.Group(); scene.add(measG);
const ray=new THREE.Raycaster(); let pending=null; const results=[];
const dotGeo=new THREE.SphereGeometry(Math.max(R*0.006,0.25),16,12), dotMat=new THREE.MeshBasicMaterial({color:0xf59e0b,depthTest:false});
function addDot(p){const s=new THREE.Mesh(dotGeo,dotMat);s.position.copy(p);s.renderOrder=3;measG.add(s);}
function readout(){const el=document.getElementById('readout');el.innerHTML=results.map((m,i)=>`<div><b>#${i+1}</b> ${m.d.toFixed(3)} mm <span class="hint">dX ${m.dx.toFixed(2)} · dY ${m.dy.toFixed(2)} · dZ ${m.dz.toFixed(2)}</span></div>`).join('')||'<span class="hint">Tap two points on the part to measure.</span>';}
let down=null;
renderer.domElement.addEventListener('pointerdown',e=>{down=[e.clientX,e.clientY];});
renderer.domElement.addEventListener('pointerup',e=>{
  if(!down||Math.hypot(e.clientX-down[0],e.clientY-down[1])>6)return;
  const r=renderer.domElement.getBoundingClientRect();
  ray.setFromCamera(new THREE.Vector2((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1),cam);
  const hit=ray.intersectObject(mesh,false)[0]; if(!hit)return;
  addDot(hit.point);
  if(!pending){pending=hit.point.clone();return;}
  const a=pending,b=hit.point.clone(); pending=null;
  const ln=new THREE.Line(new THREE.BufferGeometry().setFromPoints([a,b]),lineMat(0xf59e0b)); ln.renderOrder=3; measG.add(ln);
  const m={d:a.distanceTo(b),dx:Math.abs(b.x-a.x),dy:Math.abs(b.y-a.y),dz:Math.abs(b.z-a.z)}; results.push(m);
  label(m.d.toFixed(2)+' mm',a.clone().add(b).multiplyScalar(.5),'meas','meas'); readout();
});
// ---- buttons
const state={box:true,holes:true,color:true,wire:false};
function sync(){boxG.visible=state.box;holeG.visible=state.holes;mesh.material=state.color?matC:matG;wire.visible=state.wire;
  for(const k of Object.keys(state)){const b=document.getElementById('b-'+k);if(b)b.classList.toggle('on',state[k]);}}
for(const k of Object.keys(state)){const b=document.getElementById('b-'+k);if(b)b.onclick=()=>{state[k]=!state[k];sync();};}
document.getElementById('b-clear').onclick=()=>{measG.clear();pending=null;results.length=0;for(let i=labels.length-1;i>=0;i--)if(labels[i].group==='meas'){labels[i].el.remove();labels.splice(i,1);}readout();};
document.getElementById('b-reset').onclick=resetView;
sync(); readout();
// ---- loop
const v=new THREE.Vector3();
function resize(){const w=app.clientWidth,h=app.clientHeight;renderer.setSize(w,h,false);renderer.domElement.style.width=w+'px';renderer.domElement.style.height=h+'px';cam.aspect=w/h;cam.updateProjectionMatrix();}
window.addEventListener('resize',resize); resize(); resetView();
renderer.setAnimationLoop(()=>{ctl.update();renderer.render(scene,cam);
  const w=app.clientWidth,h=app.clientHeight;
  for(const L of labels){const vis=(L.group==='box'&&state.box)||(L.group==='holes'&&state.holes)||L.group==='meas';
    v.copy(L.pos).project(cam); const show=vis&&v.z<1;
    L.el.style.display=show?'block':'none'; if(show){L.el.style.left=((v.x+1)/2*w)+'px';L.el.style.top=((1-v.y)/2*h)+'px';}}
});
window.__partscan_ready=true;
"""

HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>PartScan 3D viewer</title><style>{css}</style>
<script type="importmap">{importmap}</script></head><body>
<div id="app"></div>
<div id="panel"><h1>{title}</h1>
<div class="row"><button id="b-box">Dimensions</button><button id="b-holes">Holes</button><button id="b-color">Colour</button><button id="b-wire">Wireframe</button><button id="b-clear">Clear</button><button id="b-reset">Reset view</button></div>
<div id="readout"></div></div>
<script type="application/json" id="partscan-data">{data}</script>
<script type="module">{js}</script></body></html>"""


def write_viewer(path: str, mesh_m, meas: dict, title: str | None = None) -> str:
    src = three_sources()
    if src is not None:
        im = {"three": "data:text/javascript;base64," + base64.b64encode(src["three"]).decode(),
              "three/addons/controls/OrbitControls.js": "data:text/javascript;base64," + base64.b64encode(src["orbit"]).decode()}
    else:
        base = f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/"
        im = {"three": base + _FILES["three"], "three/addons/controls/OrbitControls.js": base + _FILES["orbit"]}
    from .measure import best_dims
    L, W, H, _ = best_dims(meas)
    title = title or f"Part {L:.2f} × {W:.2f} × {H:.2f} mm"
    data = json.dumps(viewer_payload(mesh_m, meas)).replace("</", "<\\/")
    html = HTML.format(css=CSS, importmap=json.dumps({"imports": im}), title=title, data=data, js=JS)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return path
