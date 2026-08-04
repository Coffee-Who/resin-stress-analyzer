"""命令列介面：python -m resin_stress <model.stl> [options]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analyzer import PrintSettings, analyze
from .materials import list_materials
from .report import plot, write_csv, write_json, write_markdown


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="resin-stress",
        description="光固化 3D 列印熱應力 / 應力集中篩檢工具",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("model", nargs="?", help="STL / OBJ / PLY / 3MF 檔案路徑")
    p.add_argument("--material", "-m", default="standard",
                   help="材料名稱，用 --list-materials 查看")
    p.add_argument("--material-file", default=None,
                   help="自訂材料 JSON（可覆寫內建參數）")
    p.add_argument("--layer-height", type=float, default=0.05, help="層厚 mm")
    p.add_argument("--pixel-size", type=float, default=0.035, help="XY 像素尺寸 mm")
    p.add_argument("--exposure", type=float, default=2.5, help="單層曝光時間 s")
    p.add_argument("--lift-time", type=float, default=4.0, help="抬升 + 回位時間 s")
    p.add_argument("--tilt", type=float, default=0.0, help="繞 X 軸傾斜角度")
    p.add_argument("--spin", type=float, default=0.0, help="繞 Z 軸旋轉角度")
    p.add_argument("--scale", type=float, default=1.0, help="模型縮放倍率")
    p.add_argument("--max-layers", type=int, default=400, help="取樣層數上限")
    p.add_argument("--outdir", "-o", default="report", help="輸出資料夾")
    p.add_argument("--no-plot", action="store_true", help="不產生圖表")
    p.add_argument("--no-3d", action="store_true",
                   help="不產生 3D 熱點圖與互動網頁")
    p.add_argument("--export-mesh", metavar="EXT", default=None,
                   choices=["ply", "glb", "obj"],
                   help="另外輸出帶頂點顏色的網格（可丟進 MeshLab / Blender）")
    p.add_argument("--list-materials", action="store_true", help="列出內建材料")
    p.add_argument("--selfcheck", action="store_true",
                   help="檢查執行環境與相依套件，並跑一次切片測試")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.selfcheck:
        from .selfcheck import run
        return run()

    if args.list_materials:
        for key, mat in list_materials().items():
            print(f"{key:<15} {mat.label:<18} E={mat.E:>5} MPa  "
                  f"α={mat.alpha*1e6:>5.0f} µm/m·K  收縮={mat.cure_shrinkage*100:.2f}%")
        return 0

    if not args.model:
        build_parser().print_help()
        return 1
    if not Path(args.model).exists():
        print(f"找不到檔案：{args.model}", file=sys.stderr)
        return 2

    settings = PrintSettings(
        layer_height=args.layer_height, pixel_size=args.pixel_size,
        exposure_time=args.exposure, lift_time=args.lift_time,
        max_layers=args.max_layers, tilt_deg=args.tilt, spin_deg=args.spin,
    )

    print(f"分析中：{args.model} …")
    result = analyze(args.model, material=args.material, settings=settings,
                     scale=args.scale, custom_material_file=args.material_file)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stem = Path(args.model).stem

    write_json(result, outdir / f"{stem}_report.json")
    write_csv(result, outdir / f"{stem}_layers.csv")
    write_markdown(result, outdir / f"{stem}_report.md")
    if not args.no_plot:
        plot(result, outdir / f"{stem}_charts.png")
    if not args.no_3d:
        from .visualize import export_colored, export_html, render_views
        render_views(result.mesh_object, result, outdir / f"{stem}_3d.png")
        export_html(result.mesh_object, result, outdir / f"{stem}_viewer.html")
        if args.export_mesh:
            export_colored(result.mesh_object, result,
                           outdir / f"{stem}_colored.{args.export_mesh}")

    s = result.summary
    print("-" * 58)
    print(f"風險分數 : {s['overall_risk_score']} / 100   等級：{s['overall_level']}")
    print(f"　應力指數 {s['stress_index']:.2f}　剝離指數 {s['peel_index']:.2f}")
    print(f"最危險層 : z = {s['worst_layer']['z']} mm  (Kt={s['worst_layer']['kt']})")
    print(f"最大溫升 : {s['max_delta_t_k']} K")
    print(f"截面突變 : {s['abrupt_layers']} 層（最大 {s['max_area_change']*100:.0f}%）")
    print("-" * 58)
    for r in result.recommendations[:4]:
        print(f"• {r}")
    print(f"\n報告已輸出至 {outdir.resolve()}")
    if not args.no_3d:
        print(f"3D 檢視：open {outdir / (stem + '_viewer.html')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
