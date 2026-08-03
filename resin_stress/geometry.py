"""模型載入、擺放旋轉、逐層切片與截面幾何量測。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import trimesh
from shapely.geometry import Polygon


@dataclass
class LayerSection:
    """單一層的截面資訊。"""

    index: int
    z: float
    polygons: List[Polygon] = field(default_factory=list, repr=False)
    area: float = 0.0          # mm^2
    perimeter: float = 0.0     # mm
    islands: int = 0           # 獨立島嶼數量
    centroid: tuple = (0.0, 0.0)

    @property
    def effective_thickness(self) -> float:
        """等效厚度 t = 2A/P（長條板時即為板厚），用於估算散熱能力。"""
        if self.perimeter <= 1e-9:
            return 0.0
        return 2.0 * self.area / self.perimeter

    @property
    def equivalent_radius(self) -> float:
        """等面積圓半徑，用於估算截面突變的「台階深度」。"""
        return float(np.sqrt(max(self.area, 0.0) / np.pi))


def load_mesh(path: str, scale: float = 1.0) -> trimesh.Trimesh:
    """載入 STL / OBJ / PLY / 3MF，回傳單一 Trimesh（單位假設為 mm）。"""
    mesh = trimesh.load(path, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh) or mesh.faces.shape[0] == 0:
        raise ValueError(f"無法從 {path} 讀出有效的三角網格")
    mesh = mesh.copy()
    if scale != 1.0:
        mesh.apply_scale(scale)
    mesh.remove_unreferenced_vertices()
    mesh.merge_vertices()
    return mesh


def orient_mesh(mesh: trimesh.Trimesh, tilt_deg: float = 0.0,
                spin_deg: float = 0.0) -> trimesh.Trimesh:
    """模擬擺放角度：先繞 X 軸傾斜 tilt，再繞 Z 軸旋轉 spin。"""
    m = mesh.copy()
    if tilt_deg:
        m.apply_transform(
            trimesh.transformations.rotation_matrix(
                np.radians(tilt_deg), [1, 0, 0], m.centroid)
        )
    if spin_deg:
        m.apply_transform(
            trimesh.transformations.rotation_matrix(
                np.radians(spin_deg), [0, 0, 1], m.centroid)
        )
    m.apply_translation([0, 0, -m.bounds[0][2]])  # 貼平台
    return m


def slice_layers(mesh: trimesh.Trimesh, layer_height: float = 0.05,
                 max_layers: int = 400) -> List[LayerSection]:
    """沿 Z 逐層切片。

    為了控制運算量，實際取樣層數上限為 max_layers；超過時等距抽樣，
    但回傳的 z 仍為真實高度，趨勢分析不受影響。
    """
    if layer_height <= 0:
        raise ValueError("layer_height 必須為正值")

    z_min, z_max = float(mesh.bounds[0][2]), float(mesh.bounds[1][2])
    height = z_max - z_min
    if height <= layer_height:
        raise ValueError("模型高度小於一個層厚，無法分析")

    n_real = int(np.floor(height / layer_height))
    stride = max(1, int(np.ceil(n_real / max_layers)))
    z_values = z_min + (np.arange(0, n_real, stride) + 0.5) * layer_height

    sections: List[LayerSection] = []
    for i, z in enumerate(z_values):
        polys = _section_polygons(mesh, float(z))
        if not polys:
            continue
        area = float(sum(p.area for p in polys))
        perim = float(sum(p.length for p in polys))
        cx = float(np.mean([p.centroid.x for p in polys]))
        cy = float(np.mean([p.centroid.y for p in polys]))
        sections.append(
            LayerSection(index=i, z=float(z), polygons=polys, area=area,
                         perimeter=perim, islands=len(polys),
                         centroid=(cx, cy))
        )
    if len(sections) < 3:
        raise ValueError("有效截面層數不足，請檢查模型是否封閉或層厚設定")
    return sections


def _section_polygons(mesh: trimesh.Trimesh, z: float) -> List[Polygon]:
    """取得 z 高度的截面多邊形（世界座標 XY，含孔洞）。"""
    try:
        section = mesh.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
    except Exception:
        return []
    if section is None:
        return []

    to_2D = np.eye(4)
    to_2D[2, 3] = -z  # 僅平移，保持 XY 與世界座標一致
    # trimesh >= 4.5 改名為 to_2D，舊版仍是 to_planar
    project = getattr(section, "to_2D", None) or section.to_planar
    try:
        planar, _ = project(to_2D=to_2D)
        polys = list(planar.polygons_full)
    except Exception:
        return []

    return [p for p in polys if p.is_valid and p.area > 1e-6]


def mesh_summary(mesh: trimesh.Trimesh) -> dict:
    size = mesh.bounds[1] - mesh.bounds[0]
    return {
        "faces": int(mesh.faces.shape[0]),
        "watertight": bool(mesh.is_watertight),
        "volume_mm3": float(abs(mesh.volume)) if mesh.is_volume else None,
        "surface_area_mm2": float(mesh.area),
        "bbox_mm": [round(float(v), 3) for v in size],
    }
