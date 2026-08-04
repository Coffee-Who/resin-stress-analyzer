"""環境自我檢查。

切片失敗最常見的原因是缺少 trimesh 的選用相依套件，而這類錯誤
往往被包在一層層的 try/except 裡看不到。這個模組把整條路徑跑一次，
並印出完整的版本與錯誤資訊。

    python -m resin_stress --selfcheck
"""

from __future__ import annotations

import importlib
import platform
import sys
import traceback

REQUIRED = ["numpy", "trimesh", "shapely", "matplotlib", "networkx"]
OPTIONAL = {
    "scipy": "trimesh 的路徑處理會用到，缺了可能切不出截面",
    "rtree": "加速輪廓包含關係判斷，缺了會用較慢的替代方案",
    "mapbox_earcut": "多邊形三角化，產生示範件時需要",
    "manifold3d": "布林運算，部分模型的修復會用到",
    "pandas": "只有網頁介面需要",
    "streamlit": "只有網頁介面需要",
}


def _version(name: str) -> str | None:
    try:
        mod = importlib.import_module(name)
    except Exception:
        return None
    return getattr(mod, "__version__", "已安裝")


def run() -> int:
    print("=" * 60)
    print("resin-stress-analyzer 環境檢查")
    print("=" * 60)
    print(f"Python  {sys.version.split()[0]}  ({platform.system()} "
          f"{platform.machine()})")
    print()

    missing_required = []
    print("[必要套件]")
    for name in REQUIRED:
        v = _version(name)
        print(f"  {'OK ' if v else '缺少'}  {name:<14} {v or ''}")
        if not v:
            missing_required.append(name)

    missing_optional = []
    print("\n[選用套件]")
    for name, why in OPTIONAL.items():
        v = _version(name)
        print(f"  {'OK ' if v else '缺少'}  {name:<14} {v or why}")
        if not v:
            missing_optional.append(name)

    if missing_required:
        print("\n必要套件缺失，請執行：")
        print(f"  pip install {' '.join(missing_required)}")
        return 1

    print("\n[切片測試]")
    try:
        from .analyzer import PrintSettings, analyze
        from .demo import stl_bytes
        import tempfile
        from pathlib import Path

        d = Path(tempfile.mkdtemp())
        (d / "demo.stl").write_bytes(stl_bytes())
        res = analyze(str(d / "demo.stl"), "standard",
                      PrintSettings(layer_height=0.1, max_layers=100))
        print(f"  OK  切出 {len(res.layers)} 層，"
              f"分數 {res.summary['overall_risk_score']}")
        print(f"  OK  熱點 {len(res.hotspots)} 處")
    except Exception:
        print("  失敗！完整錯誤如下：\n")
        traceback.print_exc()
        if missing_optional:
            print("\n先試著補裝選用套件：")
            print(f"  pip install {' '.join(missing_optional)}")
        return 1

    print("\n一切正常，可以開始使用。")
    if missing_optional:
        print(f"（選用套件 {', '.join(missing_optional)} 未安裝，"
              "目前不影響運作）")
    return 0
