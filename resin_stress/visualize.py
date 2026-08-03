"""把分析結果畫回 3D 模型上。

三種輸出：

1. `render_views()`  matplotlib 離線算圖，四個視角的 PNG（不需要 GPU / 顯示器）
2. `export_colored()` 帶頂點顏色的 PLY / GLB，可直接丟進 MeshLab、Blender、CHITUBOX
3. `export_html()`   自帶 three.js 的互動網頁，可旋轉縮放、點熱點看數值

著色邏輯
--------
每個頂點的風險值由兩部分取大者：

- **該高度的逐層風險**：把 risk(z) 內插到頂點的 z
- **熱點鄰域加成**：距離某個應力集中稜線 r 以內的頂點，
  以高斯衰減套上該熱點的風險值

所以整體會呈現「高度趨勢」的底色，而尖角稜線會被明確標成紅色。
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import numpy as np
import trimesh

from .analyzer import AnalysisResult

# 由藍(安全) -> 綠 -> 黃 -> 橘 -> 紅(危險)
COLOR_STOPS = [
    (0.00, (0.16, 0.42, 0.71)),
    (0.35, (0.23, 0.65, 0.43)),
    (0.60, (0.88, 0.70, 0.12)),
    (0.85, (0.88, 0.48, 0.12)),
    (1.20, (0.79, 0.19, 0.17)),
]
HOTSPOT_INFLUENCE_MM = 3.0


def refine(mesh: trimesh.Trimesh, divisions: int = 60,
           max_faces: int = 200000) -> trimesh.Trimesh:
    """依模型尺寸細分網格，讓顏色能在同一個平面上變化。

    STL 常常由很大的三角形構成（一根柱子的側面可能只有兩個三角形），
    不細分的話頂點顏色會被內插成一整片紅色，看不出熱點的實際位置。
    細分後若超過面數上限，再簡化回來。
    """
    target = float(mesh.extents.max()) / max(divisions, 1)
    m = mesh.copy()
    for _ in range(5):
        try:
            m = mesh.subdivide_to_size(target, max_iter=12)
            break
        except Exception:
            target *= 2.0        # 目標邊長太小會爆掉，放寬再試
    if len(m.faces) > max_faces:
        try:
            m = m.simplify_quadric_decimation(max_faces)
        except Exception:
            pass
    return m


def vertex_risk(mesh: trimesh.Trimesh, result: AnalysisResult,
                influence: float = HOTSPOT_INFLUENCE_MM) -> np.ndarray:
    """回傳每個頂點的風險值（局部應力 / UTS）。"""
    layers = result.layers
    uts = max(float(result.material["uts"]), 1e-6)
    zs = np.array([l.z for l in layers])
    # 底色只用「整層都感受得到」的部分（熱應力 + 收縮，經截面突變放大）。
    # 尖角的 Kt 是局部效應，不該把整個截面都染紅，改用熱點鄰域加成處理。
    risks = np.array([l.driving_stress * l.kt_step / uts for l in layers])

    v = mesh.vertices
    base = np.interp(v[:, 2], zs, risks)

    for h in result.hotspots:
        centre_xy = np.array([h["x"], h["y"]])
        d_xy = np.linalg.norm(v[:, :2] - centre_xy, axis=1)
        # 高度方向落在熱點的延伸範圍內才算
        dz = np.maximum.reduce([
            h["z_min"] - v[:, 2], v[:, 2] - h["z_max"],
            np.zeros(len(v)),
        ])
        dist = np.sqrt(d_xy ** 2 + dz ** 2)
        weight = np.exp(-(dist / influence) ** 2)
        base = np.maximum(base, h["risk"] * weight)

    return base


def risk_to_rgb(values: np.ndarray) -> np.ndarray:
    """風險值 -> RGB (0~1)，超過 1.2 一律紅色。"""
    stops = np.array([s[0] for s in COLOR_STOPS])
    cols = np.array([s[1] for s in COLOR_STOPS])
    out = np.zeros((len(values), 3))
    for c in range(3):
        out[:, c] = np.interp(values, stops, cols[:, c])
    return out


def _fit(mesh: trimesh.Trimesh, max_faces: int,
         divisions: int = 60) -> trimesh.Trimesh:
    """先細分到足以呈現顏色梯度，太大的網格再簡化回上限。"""
    m = refine(mesh, divisions=divisions, max_faces=max_faces)
    if len(m.faces) > max_faces:
        try:
            m = m.simplify_quadric_decimation(max_faces)
        except Exception:
            pass
    return m


def colored_mesh(mesh: trimesh.Trimesh,
                 result: AnalysisResult) -> trimesh.Trimesh:
    """回傳帶頂點顏色的網格副本。"""
    m = _fit(mesh, 120000, divisions=60)
    rgb = risk_to_rgb(vertex_risk(m, result))
    rgba = np.hstack([(rgb * 255).astype(np.uint8),
                      np.full((len(rgb), 1), 255, dtype=np.uint8)])
    m.visual.vertex_colors = rgba
    return m


def export_colored(mesh: trimesh.Trimesh, result: AnalysisResult,
                   path: str | Path) -> Path:
    """輸出帶顏色的 PLY / GLB / OBJ。"""
    path = Path(path)
    colored_mesh(mesh, result).export(path)
    return path


# ------------------------------------------------------------------ 靜態算圖
def render_views(mesh: trimesh.Trimesh, result: AnalysisResult,
                 path: str | Path, max_faces: int = 60000,
                 dpi: int = 130) -> Path:
    """matplotlib 離線算四個視角的 PNG（headless 環境可用）。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    path = Path(path)
    m = _fit(mesh, max_faces, divisions=55)

    risk = vertex_risk(m, result)
    face_risk = risk[m.faces].mean(axis=1)
    face_rgb = risk_to_rgb(face_risk)

    # 簡單的方向性打光，讓形體看得出來
    normals = np.asarray(m.face_normals)
    light = np.array([0.4, -0.7, 0.6])
    light /= np.linalg.norm(light)
    shade = 0.62 + 0.38 * np.clip(normals @ light, 0, 1)
    face_rgb = np.clip(face_rgb * shade[:, None], 0, 1)

    tri = m.vertices[m.faces]
    extents = m.bounds[1] - m.bounds[0]
    centre = m.bounds.mean(axis=0)
    span = float(extents.max()) * 0.62

    views = [("Front-left", 22, -60), ("Front-right", 22, 30),
             ("Rear", 22, 140), ("Top-down", 68, -60)]

    fig = plt.figure(figsize=(12, 10.5))
    lvl = {"低": "LOW", "中": "MEDIUM", "高": "HIGH", "極高": "CRITICAL"}
    fig.suptitle(
        f"Stress hotspots on model - {Path(result.file).name}   "
        f"(score {result.summary['overall_risk_score']}/100, "
        f"{lvl.get(result.summary['overall_level'], '')})", fontsize=13)

    for i, (name, elev, azim) in enumerate(views, 1):
        ax = fig.add_subplot(2, 2, i, projection="3d")
        coll = Poly3DCollection(tri, facecolors=face_rgb,
                                edgecolors="none", linewidths=0)
        ax.add_collection3d(coll)
        for axis, c in zip("xyz", centre):
            getattr(ax, f"set_{axis}lim")(c - span, c + span)
        ax.view_init(elev=elev, azim=azim)
        ax.set_box_aspect((1, 1, 1))
        ax.set_title(name, fontsize=10)
        ax.set_xlabel("X"), ax.set_ylabel("Y"), ax.set_zlabel("Z")
        ax.tick_params(labelsize=7)
        ax.grid(False)

    cmap = LinearSegmentedColormap.from_list(
        "risk", [s[1] for s in COLOR_STOPS])
    cax = fig.add_axes((0.32, 0.055, 0.36, 0.016))
    cb = fig.colorbar(plt.cm.ScalarMappable(cmap=cmap), cax=cax,
                      orientation="horizontal")
    cb.set_ticks([0, 0.29, 0.5, 0.71, 1.0])
    cb.set_ticklabels(["0", "0.35", "0.6", "0.85", "1.2+"])
    cb.set_label("Risk ratio  (local stress / UTS)", fontsize=9)

    fig.tight_layout(rect=(0, 0.08, 1, 0.96))
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    return path


# ------------------------------------------------------------------ 互動網頁
_HTML = """<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>應力集中 3D 檢視 - __NAME__</title>
<style>
 :root{--bg:#12151a;--panel:#1b2029;--line:#2b323d;--text:#e6e9ef;--dim:#9aa4b2}
 *{box-sizing:border-box} html,body{margin:0;height:100%}
 body{background:var(--bg);color:var(--text);
      font-family:-apple-system,"Noto Sans TC","PingFang TC",sans-serif;overflow:hidden}
 #view{position:fixed;inset:0}
 .card{position:fixed;background:rgba(27,32,41,.94);border:1px solid var(--line);
       border-radius:12px;padding:14px 16px;backdrop-filter:blur(8px)}
 #info{top:16px;left:16px;max-width:290px}
 #legend{bottom:16px;left:16px}
 h1{margin:0 0 4px;font-size:15px;letter-spacing:.02em}
 .sub{color:var(--dim);font-size:12px;margin-bottom:10px}
 .score{font-size:30px;font-weight:650;line-height:1}
 .lvl{font-size:13px;color:var(--dim);margin-left:8px}
 table{border-collapse:collapse;font-size:12px;margin-top:10px;width:100%}
 td{padding:2px 0;color:var(--dim)} td:last-child{color:var(--text);text-align:right}
 .bar{height:12px;width:230px;border-radius:6px;margin:6px 0 4px;
      background:linear-gradient(90deg,#2a6bb5,#3aa66e,#e0b21e,#e07b1e,#c9302c)}
 .ticks{display:flex;justify-content:space-between;font-size:10px;color:var(--dim)}
 .hs{font-size:12px;margin-top:6px;padding-top:8px;border-top:1px solid var(--line)}
 .hs div{margin:3px 0;color:var(--dim)}
 .hs b{color:#e8a0a0;font-weight:600}
 #hint{position:fixed;bottom:16px;right:16px;color:var(--dim);font-size:11px}
</style></head><body>
<div id="view"></div>
<div id="info" class="card">
  <h1>應力集中分布</h1>
  <div class="sub">__NAME__</div>
  <div><span class="score" id="score"></span><span class="lvl" id="lvl"></span></div>
  <table id="stats"></table>
  <div class="hs" id="hslist"></div>
</div>
<div id="legend" class="card">
  <div style="font-size:12px;margin-bottom:2px">風險比（局部應力 / 抗拉強度）</div>
  <div class="bar"></div>
  <div class="ticks"><span>0</span><span>0.35</span><span>0.6</span><span>0.85</span><span>1.2+</span></div>
</div>
<div id="hint">拖曳旋轉 · 滾輪縮放 · 右鍵平移</div>
<script type="importmap">
{"imports":{"three":"https://unpkg.com/three@0.160.0/build/three.module.js",
"three/addons/":"https://unpkg.com/three@0.160.0/examples/jsm/"}}
</script>
<script type="module">
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
const DATA = __DATA__;

const el=document.getElementById('view');
const renderer=new THREE.WebGLRenderer({antialias:true});
renderer.setPixelRatio(devicePixelRatio); renderer.setSize(innerWidth,innerHeight);
el.appendChild(renderer.domElement);
const scene=new THREE.Scene(); scene.background=new THREE.Color(0x12151a);

const g=new THREE.BufferGeometry();
g.setAttribute('position',new THREE.Float32BufferAttribute(DATA.positions,3));
g.setAttribute('color',new THREE.Float32BufferAttribute(DATA.colors,3));
g.setIndex(DATA.index);
g.computeVertexNormals();
g.computeBoundingSphere();
const c=g.boundingSphere.center, R=g.boundingSphere.radius;

const mesh=new THREE.Mesh(g,new THREE.MeshStandardMaterial(
  {vertexColors:true,roughness:.62,metalness:.02,flatShading:false}));
scene.add(mesh);
scene.add(new THREE.LineSegments(new THREE.EdgesGeometry(g,32),
  new THREE.LineBasicMaterial({color:0x000000,transparent:true,opacity:.18})));

// 熱點標記
DATA.hotspots.forEach(h=>{
  const s=new THREE.Mesh(new THREE.SphereGeometry(R*0.035,20,16),
    new THREE.MeshBasicMaterial({color:0xff3b30,transparent:true,opacity:.85}));
  s.position.set(h.x,h.y,(h.z_min+h.z_max)/2); scene.add(s);
  const ring=new THREE.Mesh(new THREE.SphereGeometry(R*0.07,20,16),
    new THREE.MeshBasicMaterial({color:0xff3b30,transparent:true,opacity:.18}));
  ring.position.copy(s.position); scene.add(ring);
});

scene.add(new THREE.HemisphereLight(0xffffff,0x30363f,1.5));
const d=new THREE.DirectionalLight(0xffffff,1.5); d.position.set(1,-1.4,1.6); scene.add(d);
const grid=new THREE.GridHelper(R*4,20,0x39414d,0x252b34);
grid.rotation.x=Math.PI/2; grid.position.z=0; scene.add(grid);

const cam=new THREE.PerspectiveCamera(45,innerWidth/innerHeight,R/100,R*100);
cam.up.set(0,0,1); cam.position.set(c.x+R*2,c.y-R*2.2,c.z+R*1.5);
const ctr=new OrbitControls(cam,renderer.domElement); ctr.target.copy(c);
ctr.enableDamping=true; ctr.update();

addEventListener('resize',()=>{cam.aspect=innerWidth/innerHeight;
  cam.updateProjectionMatrix(); renderer.setSize(innerWidth,innerHeight);});
(function loop(){requestAnimationFrame(loop); ctr.update(); renderer.render(scene,cam);})();

const s=DATA.summary;
score.textContent=s.overall_risk_score; lvl.textContent=s.overall_level;
stats.innerHTML=[['應力指數',s.stress_index],['剝離指數',s.peel_index],
 ['最大溫升',s.max_delta_t_k+' K'],['最危險高度','z = '+s.worst_layer.z+' mm'],
 ['截面突變層',s.abrupt_layers+' 層']]
 .map(r=>`<tr><td>${r[0]}</td><td>${r[1]}</td></tr>`).join('');
hslist.innerHTML='<b>應力集中熱點</b>'+DATA.hotspots.slice(0,5).map(h=>
 `<div>(${h.x}, ${h.y}) z ${h.z_min}~${h.z_max} · Kt <b>${h.kt}</b></div>`).join('');
</script></body></html>
"""


def export_html(mesh: trimesh.Trimesh, result: AnalysisResult,
                path: str | Path, max_faces: int = 40000) -> Path:
    """輸出自帶 three.js 的互動檢視網頁（單一 HTML 檔）。"""
    import json

    path = Path(path)
    m = _fit(mesh, max_faces, divisions=45)

    rgb = risk_to_rgb(vertex_risk(m, result))
    # 用索引式幾何，檔案大小約為展開三角形的 1/3
    data = {
        "positions": np.round(m.vertices, 3).ravel().tolist(),
        "colors": np.round(rgb, 3).ravel().tolist(),
        "index": m.faces.astype(np.int32).ravel().tolist(),
        "hotspots": result.hotspots,
        "summary": result.summary,
    }
    html = (_HTML.replace("__DATA__", json.dumps(data))
                 .replace("__NAME__", Path(result.file).name))
    path.write_text(html, encoding="utf-8")
    return path
