"""驗證核心演算法的行為是否符合物理直覺。"""

import sys
from pathlib import Path

import numpy as np
import pytest
import trimesh
from shapely.geometry import Point, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from resin_stress import PrintSettings, analyze, get_material  # noqa: E402
from resin_stress.notch import kt_from_geometry, polygon_notches  # noqa: E402
from resin_stress.thermal import (constraint_factor,  # noqa: E402
                                  exotherm_delta_t, penetration_depth)

L_SHAPE = Polygon([(0, 0), (40, 0), (40, 16), (16, 16), (16, 40), (0, 40)])


# --------------------------------------------------------------- 材料 / 熱模型
def test_diffusivity_in_expected_range():
    """光敏樹脂熱擴散係數應在 0.05~0.2 mm^2/s 量級。"""
    a = get_material("standard").diffusivity_mm2_s
    assert 0.05 < a < 0.25


def test_thicker_section_gets_hotter():
    """厚實截面散熱慢，溫升必須高於薄壁。"""
    mat = get_material("standard")
    thin, _ = exotherm_delta_t(mat, 0.5, 0.05, 6.5)
    thick, _ = exotherm_delta_t(mat, 15.0, 0.05, 6.5)
    assert thick > thin * 2
    assert thin < thick <= mat.adiabatic_delta_t


def test_delta_t_magnitude_is_plausible():
    """一般參數下的局部溫升應落在個位數到數十 K，而非數百 K。"""
    mat = get_material("standard")
    dT, _ = exotherm_delta_t(mat, 10.0, 0.05, 6.5)
    assert 3.0 < dT < 40.0


def test_constraint_increases_with_bulk_and_step():
    lp = penetration_depth(get_material("standard"), 6.5)
    assert constraint_factor(0.0, 0.5, lp) < constraint_factor(0.0, 20.0, lp)
    assert constraint_factor(0.0, 5.0, lp) < constraint_factor(0.8, 5.0, lp)
    assert 0.0 <= constraint_factor(1.0, 50.0, lp) <= 1.0


# --------------------------------------------------------------- 應力集中
def test_kt_decreases_with_larger_fillet():
    kts = [kt_from_geometry(10.0, r, 90.0) for r in (0.02, 0.5, 1.0, 2.0, 5.0)]
    assert kts == sorted(kts, reverse=True)
    assert kts[0] == pytest.approx(8.0)      # 尖角觸頂
    assert 1.5 < kts[-1] < 2.5               # R5 應為溫和的集中


def test_sharp_corner_detected_and_fillet_recovered():
    """尖角應被判為極尖；導 R 之後半徑要能被正確還原。"""
    sharp = polygon_notches(L_SHAPE, 0.0, 0.0175)
    assert len(sharp) == 1
    assert sharp[0].kt == pytest.approx(8.0)
    assert sharp[0].angle_deg == pytest.approx(90.0, abs=1.0)

    for R in (0.5, 1.0, 2.0):
        rounded = L_SHAPE.buffer(R, resolution=32).buffer(-R, resolution=32)
        feats = polygon_notches(rounded, 0.0, 0.0175)
        assert feats, f"R={R} 應仍偵測得到凹角"
        best = max(feats, key=lambda f: f.kt)
        assert best.radius == pytest.approx(R, rel=0.1)
        assert best.kt < 4.0


def test_smooth_circle_is_not_a_stress_riser():
    """離散化的圓孔不該被誤判成一圈尖角。"""
    ring = Point(0, 0).buffer(15, resolution=16).difference(
        Point(0, 0).buffer(12.5, resolution=16))
    feats = polygon_notches(ring, 0.0, 0.0175)
    assert all(f.kt < 1.6 for f in feats)


def test_convex_polygon_has_no_notch():
    box = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    assert polygon_notches(box, 0.0, 0.0175) == []


# --------------------------------------------------------------- 端對端
@pytest.fixture(scope="module")
def parts(tmp_path_factory):
    d = tmp_path_factory.mktemp("stl")
    out = {}
    for name, poly in (("sharp", L_SHAPE),
                       ("fillet", L_SHAPE.buffer(2, resolution=32)
                                         .buffer(-2, resolution=32))):
        body = trimesh.creation.extrude_polygon(poly, height=12.0)
        neck = trimesh.creation.box(extents=[8, 8, 10])
        neck.apply_translation([8, 8, 17])
        mesh = trimesh.util.concatenate([body, neck])
        mesh.apply_translation([0, 0, -mesh.bounds[0][2]])
        path = d / f"{name}.stl"
        mesh.export(path)
        out[name] = str(path)
    return out


def test_fillet_lowers_the_score(parts):
    """同一顆零件導圓角之後，風險分數必須明顯下降。"""
    st = PrintSettings(layer_height=0.05, max_layers=150)
    sharp = analyze(parts["sharp"], "standard", st).summary
    fillet = analyze(parts["fillet"], "standard", st).summary
    assert fillet["overall_risk_score"] < sharp["overall_risk_score"] - 10


def test_abrupt_section_change_is_flagged(parts):
    st = PrintSettings(layer_height=0.05, max_layers=150)
    res = analyze(parts["sharp"], "standard", st)
    assert res.summary["abrupt_layers"] >= 1
    assert res.summary["max_area_change"] > 0.5
    assert any(abs(z - 12.0) < 0.5 for z in res.summary["abrupt_z"])


def test_result_is_json_serialisable(parts):
    import json
    res = analyze(parts["sharp"], "standard",
                  PrintSettings(layer_height=0.1, max_layers=80))
    payload = json.loads(json.dumps(res.to_dict(), ensure_ascii=False))
    assert payload["summary"]["layers_analyzed"] > 10
    assert payload["recommendations"]


def test_low_shrinkage_material_scores_better(parts):
    st = PrintSettings(layer_height=0.05, max_layers=120)
    standard = analyze(parts["sharp"], "standard", st).summary
    rigid = analyze(parts["sharp"], "rigid", st).summary
    assert rigid["stress_index"] != standard["stress_index"]


# --------------------------------------------------------------- 3D 視覺化
def test_hotspot_colouring_is_local(parts, tmp_path):
    """熱點著色必須是局部的：不能把整個零件都染紅。"""
    from resin_stress.geometry import load_mesh
    from resin_stress.visualize import (export_html, refine, render_views,
                                        risk_to_rgb, vertex_risk)

    res = analyze(parts["sharp"], "standard",
                  PrintSettings(layer_height=0.05, max_layers=150))
    mesh = refine(load_mesh(parts["sharp"]), divisions=55)
    risk = vertex_risk(mesh, res)

    assert risk.max() > 1.0                 # 尖角確實被標成危險
    assert np.median(risk) < 0.6            # 但大部分區域仍是安全色
    assert (risk > 0.85).mean() < 0.45      # 高風險區只佔一部分

    rgb = risk_to_rgb(risk)
    assert rgb.shape == (len(mesh.vertices), 3)
    assert ((rgb >= 0) & (rgb <= 1)).all()

    png = render_views(load_mesh(parts["sharp"]), res, tmp_path / "v.png")
    html = export_html(load_mesh(parts["sharp"]), res, tmp_path / "v.html")
    assert png.stat().st_size > 10000
    assert "three" in html.read_text(encoding="utf-8")
