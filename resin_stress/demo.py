"""產生一個「故意設計得很糟」的示範件，用來驗證分析器抓不抓得到問題。

刻意加入三種典型缺陷：

1. 底部厚實塊體（散熱慢 -> 固化溫升高）
2. 尖銳 90 度內凹角（L 形轉角，Kt 放大器）
3. 截面突然縮小的細頸（台階狀應力集中 + 剝離力突變）
"""

from __future__ import annotations

import trimesh
from shapely.geometry import Polygon

L_SHAPE = Polygon([(0, 0), (40, 0), (40, 16), (16, 16), (16, 40), (0, 40)])


def build(fillet: float = 0.0) -> trimesh.Trimesh:
    """fillet > 0 時把 L 形內凹角導成圓角，可用來做前後對照。"""
    poly = L_SHAPE
    if fillet > 0:
        poly = poly.buffer(fillet, resolution=32).buffer(-fillet, resolution=32)

    base = trimesh.creation.extrude_polygon(poly, height=12.0)

    neck = trimesh.creation.box(extents=[8, 8, 14])
    neck.apply_translation([8, 8, 19])

    cap = trimesh.creation.cylinder(radius=11, height=4, sections=64)
    cap.apply_translation([8, 8, 28])

    mesh = trimesh.util.concatenate([base, neck, cap])
    mesh.apply_translation([0, 0, -mesh.bounds[0][2]])
    return mesh


def stl_bytes(fillet: float = 0.0) -> bytes:
    return build(fillet).export(file_type="stl")
