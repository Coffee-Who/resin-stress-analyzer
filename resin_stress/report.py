"""輸出報告：JSON / CSV / Markdown / 圖表。

圖表標籤刻意使用英文，避免使用者環境缺少中文字型時出現方框。
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from .analyzer import AnalysisResult


def write_json(result: AnalysisResult, path: str | Path) -> Path:
    path = Path(path)
    path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def write_csv(result: AnalysisResult, path: str | Path) -> Path:
    path = Path(path)
    rows = [asdict(l) for l in result.layers]
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_markdown(result: AnalysisResult, path: str | Path) -> Path:
    path = Path(path)
    s = result.summary
    m = result.material
    lines = [
        "# 光固化列印熱應力／應力集中分析報告",
        "",
        f"- 模型檔案：`{Path(result.file).name}`",
        f"- 材料：{m['label']}（UTS {m['uts']} MPa，線收縮 {m['cure_shrinkage']*100:.2f}%）",
        f"- 層厚 {result.settings['layer_height']} mm、單層週期 {result.settings['layer_time']:.1f} s",
        f"- 外框尺寸：{result.mesh['bbox_mm']} mm，封閉網格：{'是' if result.mesh['watertight'] else '否'}",
        "",
        "## 一、總評",
        "",
        f"**風險分數 {s['overall_risk_score']} / 100　等級：{s['overall_level']}**",
        "",
        "分數由兩個面向合成：",
        "",
        f"- **應力指數 {s['stress_index']:.2f}**：熱應力 + 固化收縮應力，經幾何應力集中放大後與材料強度的比值",
        f"- **剝離指數 {s['peel_index']:.2f}**：單層最大截面帶來的吸附力與收縮體積",
        "",
        "| 指標 | 數值 |",
        "| --- | --- |",
        f"| 分析層數 | {s['layers_analyzed']}（有效 {s['layers_significant']}） |",
        f"| 應力指數 / 剝離指數 | {s['stress_index']:.2f} / {s['peel_index']:.2f} |",
        f"| 面積加權風險（平均 / P90 / 最大） | {s['mean_risk_area_weighted']:.2f} / {s['p90_risk_area_weighted']:.2f} / {s['max_risk']:.2f} |",
        f"| 最危險高度 | z = {s['worst_layer']['z']} mm（Kt = {s['worst_layer']['kt']}） |",
        f"| 估計最大固化溫升 | {s['max_delta_t_k']} K |",
        f"| 單層最大截面 | {s['max_section_area_mm2']} mm²（z = {s['peak_area_z']} mm） |",
        f"| 截面突變層數（>25%） | {s['abrupt_layers']} |",
        f"| 最多獨立島嶼 | {s['max_islands']} |",
        "",
        "> 風險比 = 局部應力 / 材料抗拉強度。>1 代表理論上足以開裂，",
        "> 但本模型為一階估算，請當作「相對比較與排序」工具而非 FEA 結論。",
        "",
        "## 二、應力集中熱點（Top）",
        "",
        "| # | XY 座標 | 高度範圍 z (mm) | 夾角 | 現有圓角 R | Kt | 局部應力 MPa | 等級 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, h in enumerate(result.hotspots, 1):
        lines.append(
            f"| {i} | ({h['x']}, {h['y']}) | {h['z_min']} ~ {h['z_max']} | "
            f"{h['angle_deg']}° | {h['radius_mm']} | {h['kt']} | "
            f"{h['local_stress_mpa']} | {h['level']} |"
        )
    if not result.hotspots:
        lines.append("| - | 未偵測到明顯的尖銳凹角 | | | | | | |")

    lines += ["", "## 三、改善建議", ""]
    for r in result.recommendations:
        lines.append(f"- {r}")

    lines += [
        "",
        "---",
        "",
        "理論依據：熱應變 ε = αΔT，受約束時 σ = EαΔT/(1−ν)；",
        "缺口應力集中 Kt ≈ 1 + 0.5·f(θ)·√(t/R)（Peterson 型式）。",
        "光固化過程的溫升來自交聯反應放熱，厚實區散熱慢、溫升高，",
        "與周圍低溫區收縮速率不一致即形成內部變形約束與應力集中。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def plot(result: AnalysisResult, path: str | Path, dpi: int = 130) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path = Path(path)
    L = result.layers
    z = [l.z for l in L]

    fig, axes = plt.subplots(2, 2, figsize=(13, 8.5))
    level_en = {"低": "LOW", "中": "MEDIUM", "高": "HIGH", "極高": "CRITICAL"}
    fig.suptitle(
        f"Resin Print Stress Screening - {Path(result.file).name}   "
        f"(risk score {result.summary['overall_risk_score']}/100, "
        f"{level_en.get(result.summary['overall_level'], '')})",
        fontsize=12)

    ax = axes[0][0]
    ax.plot([l.area for l in L], z, color="#2f6fb5", lw=1.4)
    ax.set_xlabel("Cross-section area (mm²)")
    ax.set_ylabel("Z height (mm)")
    ax.set_title("Layer area vs height")
    for zz in result.summary["abrupt_z"]:
        ax.axhline(zz, color="#d9534f", lw=0.7, alpha=0.5, ls="--")
    ax.grid(alpha=0.25)

    ax = axes[0][1]
    ax.plot([l.delta_t for l in L], z, color="#e08a1e", lw=1.4)
    ax.set_xlabel("Estimated exotherm ΔT (K)")
    ax.set_ylabel("Z height (mm)")
    ax.set_title("Cure exotherm temperature rise")
    ax.grid(alpha=0.25)

    ax = axes[1][0]
    ax.plot([l.kt_notch for l in L], z, label="Kt (sharp corners)",
            color="#7b4fa8", lw=1.2)
    ax.plot([l.kt_step for l in L], z, label="Kt (section step)",
            color="#3aa66e", lw=1.2)
    ax.set_xlabel("Stress concentration factor Kt")
    ax.set_ylabel("Z height (mm)")
    ax.set_title("Geometric stress concentration")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)

    ax = axes[1][1]
    risk = [l.risk for l in L]
    colors = ["#3aa66e" if r < 0.35 else "#e0b21e" if r < 0.6
              else "#e07b1e" if r < 0.85 else "#c9302c" for r in risk]
    ax.scatter(risk, z, c=colors, s=8)
    for x, c in ((0.35, "#e0b21e"), (0.6, "#e07b1e"), (0.85, "#c9302c")):
        ax.axvline(x, color=c, ls="--", lw=0.8)
    ax.set_xlabel("Risk ratio  (local stress / UTS)")
    ax.set_ylabel("Z height (mm)")
    ax.set_title("Cracking / warping risk by layer")
    ax.grid(alpha=0.25)

    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    return path
