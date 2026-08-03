"""產生一個「故意設計得很糟」的示範模型，用來驗證分析器抓不抓得到問題。

刻意加入三種典型缺陷：
1. 底部厚實塊體（散熱慢 -> 固化溫升高）
2. 尖銳 90° 內凹角（L 形轉角，Kt 放大器）
3. 截面突然縮小的細頸（台階狀應力集中 + 剝離力突變）

用法： python examples/make_demo_stl.py [輸出路徑]
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import trimesh
from shapely.geometry import Polygon


def build() -> trimesh.Trimesh:
    # 1) L 形底座：含一個尖銳 90 度內凹角
    l_shape = Polygon([
        (0, 0), (40, 0), (40, 16), (16, 16), (16, 40), (0, 40),
    ])
    base = trimesh.creation.extrude_polygon(l_shape, height=12.0)

    # 2) 中段方柱：截面突然縮小
    neck = trimesh.creation.box(extents=[8, 8, 14])
    neck.apply_translation([8, 8, 12 + 7])

    # 3) 頂部圓盤：截面又突然放大（懸空台階）
    cap = trimesh.creation.cylinder(radius=11, height=4, sections=64)
    cap.apply_translation([8, 8, 26 + 2])

    mesh = trimesh.util.concatenate([base, neck, cap])
    mesh.apply_translation([0, 0, -mesh.bounds[0][2]])
    return mesh


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "examples/demo_part.stl")
    out.parent.mkdir(parents=True, exist_ok=True)
    mesh = build()
    mesh.export(out)
    print(f"已輸出 {out}  尺寸 {np.round(mesh.extents, 2)} mm")


if __name__ == "__main__":
    main()
