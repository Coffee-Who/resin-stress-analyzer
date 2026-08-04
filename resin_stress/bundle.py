"""一次產生所有輸出檔並以位元組回傳。

網頁介面沒有「輸出資料夾」的概念，需要的是可以直接塞進下載按鈕的位元組；
這個模組把分析與產檔包成一個函式，順便讓這條路徑可以被測試。
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from .analyzer import AnalysisResult, PrintSettings, analyze
from .report import plot, write_csv, write_json, write_markdown
from .visualize import export_colored, export_html, render_views


@dataclass
class Bundle:
    result: AnalysisResult
    files: Dict[str, bytes]   # md / csv / json / charts / views / ply
    viewer_html: str


def analyze_bytes(data: bytes, filename: str,
                  material: str = "standard",
                  settings: PrintSettings | None = None,
                  scale: float = 1.0,
                  with_3d: bool = True) -> Bundle:
    """從檔案內容（而非路徑）跑完整分析，回傳所有輸出。"""
    workdir = Path(tempfile.mkdtemp(prefix="resin_stress_"))
    model = workdir / Path(filename).name
    model.write_bytes(data)

    result = analyze(str(model), material=material,
                     settings=settings or PrintSettings(), scale=scale)
    stem = model.stem

    files: Dict[str, bytes] = {
        "md": write_markdown(result, workdir / f"{stem}.md").read_bytes(),
        "csv": write_csv(result, workdir / f"{stem}.csv").read_bytes(),
        "json": write_json(result, workdir / f"{stem}.json").read_bytes(),
        "charts": plot(result, workdir / f"{stem}_charts.png").read_bytes(),
    }
    viewer = ""
    if with_3d:
        mesh = result.mesh_object
        files["views"] = render_views(
            mesh, result, workdir / f"{stem}_3d.png").read_bytes()
        files["ply"] = export_colored(
            mesh, result, workdir / f"{stem}.ply").read_bytes()
        viewer = export_html(
            mesh, result, workdir / f"{stem}_viewer.html").read_text("utf-8")

    return Bundle(result=result, files=files, viewer_html=viewer)
