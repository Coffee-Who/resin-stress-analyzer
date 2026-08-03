"""主分析流程：切片 -> 幾何指標 -> 熱應力 -> 應力集中 -> 風險評分。"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List

import numpy as np

from .geometry import LayerSection, load_mesh, mesh_summary, orient_mesh, slice_layers
from .materials import Material, get_material
from .notch import NotchFeature, kt_from_geometry, layer_notches
from .thermal import (DEFAULT_H_CONV_W_M2K, constraint_factor,
                      layer_thermal_state, penetration_depth)

# 單層風險比（局部應力 / 抗拉強度）的等級
RISK_LEVELS = [
    (0.35, "低"),
    (0.60, "中"),
    (0.85, "高"),
    (float("inf"), "極高"),
]

# 整體 0~100 分數的等級
SCORE_LEVELS = [(30, "低"), (50, "中"), (70, "高"), (float("inf"), "極高")]


def _score_level(score: float) -> str:
    for bound, name in SCORE_LEVELS:
        if score < bound:
            return name
    return "極高"


@dataclass
class PrintSettings:
    layer_height: float = 0.05      # mm
    pixel_size: float = 0.035       # mm，XY 解析度
    exposure_time: float = 2.5      # s
    lift_time: float = 4.0          # s
    max_layers: int = 400
    tilt_deg: float = 0.0
    spin_deg: float = 0.0
    h_conv: float = DEFAULT_H_CONV_W_M2K   # W/(m^2*K)，對樹脂槽等效散熱

    @property
    def layer_time(self) -> float:
        return self.exposure_time + self.lift_time

    @property
    def min_radius(self) -> float:
        """可列印的最小圓角半徑，作為 Kt 計算的半徑下限。"""
        return max(self.pixel_size, self.layer_height) * 0.5


@dataclass
class LayerResult:
    index: int
    z: float
    area: float
    perimeter: float
    islands: int
    thickness: float
    area_change: float       # 相對前一層的截面變化率
    delta_t: float
    thermal_stress: float
    shrink_stress: float
    constraint: float
    kt_step: float
    kt_notch: float
    kt: float
    driving_stress: float
    local_stress: float
    risk: float
    level: str
    significant: bool = True


@dataclass
class AnalysisResult:
    file: str
    material: Dict[str, Any]
    settings: Dict[str, Any]
    mesh: Dict[str, Any]
    layers: List[LayerResult] = field(default_factory=list)
    hotspots: List[Dict[str, Any]] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)
    mesh_object: Any = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "material": self.material,
            "settings": self.settings,
            "mesh": self.mesh,
            "summary": self.summary,
            "layers": [asdict(l) for l in self.layers],
            "hotspots": self.hotspots,
            "recommendations": self.recommendations,
        }


def analyze(path: str, material: str | Material = "standard",
            settings: PrintSettings | None = None,
            scale: float = 1.0,
            custom_material_file: str | None = None) -> AnalysisResult:
    """分析一個模型檔案，回傳完整結果。"""
    settings = settings or PrintSettings()
    mat = material if isinstance(material, Material) else get_material(
        material, custom_material_file)

    mesh = load_mesh(path, scale=scale)
    if settings.tilt_deg or settings.spin_deg:
        mesh = orient_mesh(mesh, settings.tilt_deg, settings.spin_deg)

    sections = slice_layers(mesh, settings.layer_height, settings.max_layers)
    layers, hotspots = _evaluate(sections, mat, settings)

    result = AnalysisResult(
        file=path,
        material=mat.to_dict(),
        settings={**asdict(settings), "layer_time": settings.layer_time},
        mesh=mesh_summary(mesh),
    )
    result.layers = layers
    result.hotspots = hotspots
    result.summary = _summarize(layers, hotspots, mat, settings)
    result.recommendations = _recommend(result.summary, layers, mat, settings)
    result.mesh_object = mesh      # 供 3D 視覺化重用（不進 JSON）
    return result


def _evaluate(sections: List[LayerSection], mat: Material,
              st: PrintSettings):
    thickness = np.array([s.effective_thickness for s in sections])
    mean_thickness = float(np.mean(thickness[thickness > 0])) or 1.0
    penetration = penetration_depth(mat, st.layer_time)
    # 面積參考值：用來過濾「即將收尖」的微小截面，
    # 錐尖最後幾層的面積變化率在數學上很大，但結構上沒有意義。
    area_ref = max(float(max(s.area for s in sections)), 1e-6)
    area_floor = max(0.5, 0.002 * area_ref)

    layers: List[LayerResult] = []
    all_notches: List[tuple] = []

    for i, sec in enumerate(sections):
        prev = sections[i - 1] if i > 0 else sec
        denom = max(sec.area, prev.area, area_floor)
        area_change = (sec.area - prev.area) / denom

        # 截面突變造成的台階狀應力集中：
        # 以等面積圓半徑的層間變化量當缺口深度，扣掉「自然階梯」高度
        # （45 度斜面每層剛好前進一個層厚，不該算成應力集中）。
        d_r = abs(sec.equivalent_radius - prev.equivalent_radius)
        step_depth = max(0.0, d_r - st.layer_height)
        kt_step = kt_from_geometry(step_depth, st.layer_height, 90.0)

        feats: List[NotchFeature] = layer_notches(
            sec.polygons, sec.z, st.min_radius)
        kt_notch = max((f.kt for f in feats), default=1.0)

        cf = constraint_factor(area_change, sec.effective_thickness,
                               penetration)
        ts = layer_thermal_state(mat, sec.effective_thickness,
                                 st.layer_height, st.layer_time, cf,
                                 st.h_conv)

        kt = float(max(kt_step, kt_notch))
        local = ts.driving_stress * kt
        risk = local / mat.uts

        layers.append(LayerResult(
            index=sec.index, z=round(sec.z, 4),
            area=round(sec.area, 4), perimeter=round(sec.perimeter, 4),
            islands=sec.islands, thickness=round(sec.effective_thickness, 4),
            area_change=round(area_change, 4),
            delta_t=round(ts.delta_t, 3),
            thermal_stress=round(ts.thermal_stress, 3),
            shrink_stress=round(ts.shrink_stress, 3),
            constraint=round(ts.constraint, 3),
            kt_step=round(kt_step, 3), kt_notch=round(kt_notch, 3),
            kt=round(kt, 3),
            driving_stress=round(ts.driving_stress, 3),
            local_stress=round(local, 3),
            risk=round(risk, 4), level=_level(risk),
            significant=bool(sec.area >= area_floor),
        ))

        for f in feats:
            all_notches.append((ts.driving_stress * f.kt, f))

    all_notches.sort(key=lambda t: t[0], reverse=True)
    hotspots = _dedupe_hotspots(all_notches, mat)
    return layers, hotspots


def _dedupe_hotspots(scored, mat: Material, keep: int = 12,
                     min_dist: float = 1.5) -> List[Dict[str, Any]]:
    """同一條垂直稜線會在數百層重複出現。

    以 XY 平面距離做群聚，同一條稜線只留應力最大的代表點，
    並記錄它延伸的高度範圍與層數，方便直接對應到模型上的一條邊。
    """
    groups: List[Dict[str, Any]] = []
    for stress, f in scored:
        p = np.array([f.x, f.y])
        hit = None
        for g in groups:
            if np.linalg.norm(p - g["_xy"]) < min_dist:
                hit = g
                break
        if hit is not None:
            hit["z_min"] = min(hit["z_min"], f.z)
            hit["z_max"] = max(hit["z_max"], f.z)
            hit["layers"] += 1
            continue
        groups.append({
            "_xy": p,
            "x": round(f.x, 3), "y": round(f.y, 3), "z": round(f.z, 3),
            "z_min": f.z, "z_max": f.z, "layers": 1,
            "radius_mm": round(f.radius, 4),
            "angle_deg": round(f.angle_deg, 1),
            "kt": round(f.kt, 3),
            "local_stress_mpa": round(stress, 3),
            "risk": round(stress / mat.uts, 3),
            "level": _level(stress / mat.uts),
        })

    groups.sort(key=lambda g: g["local_stress_mpa"], reverse=True)
    out = []
    for g in groups[:keep]:
        g.pop("_xy")
        g["z_min"] = round(g["z_min"], 3)
        g["z_max"] = round(g["z_max"], 3)
        out.append(g)
    return out


def _level(risk: float) -> str:
    for bound, name in RISK_LEVELS:
        if risk < bound:
            return name
    return "極高"


def _weighted_quantile(values: np.ndarray, weights: np.ndarray,
                       q: float) -> float:
    """面積加權分位數：截面越大的層，權重越高。

    這樣可以避免球體極點那種「面積極小但變化率極大」的過渡層
    主導整體評分。
    """
    order = np.argsort(values)
    v, w = values[order], weights[order]
    cum = np.cumsum(w)
    if cum[-1] <= 0:
        return float(np.percentile(values, q * 100))
    cutoff = q * cum[-1]
    return float(v[int(np.searchsorted(cum, cutoff))])


def _summarize(layers: List[LayerResult], hotspots, mat: Material,
               st: PrintSettings) -> Dict[str, Any]:
    core = [l for l in layers if l.significant] or layers
    risks = np.array([l.risk for l in core])
    areas = np.array([l.area for l in core])
    changes = np.array([abs(l.area_change) for l in core])
    dts = np.array([l.delta_t for l in core])
    weights = np.maximum(areas, 1e-6)

    mean_w = float(np.sum(weights * risks) / np.sum(weights))
    p90_w = _weighted_quantile(risks, weights, 0.90)

    worst = core[int(np.argmax(risks))]
    peak = core[int(np.argmax(areas))]
    abrupt = [l for l in core if abs(l.area_change) > 0.25]

    # 應力面向：面積加權的平均與高分位
    stress_index = 0.4 * mean_w + 0.6 * p90_w
    # 剝離／體積面向：大截面 = 大吸附力 + 大收縮體積，本身就是失敗主因
    peel_index = float(np.clip(areas.max() / 2000.0, 0.0, 1.5))

    index = 0.8 * stress_index + 0.6 * peel_index
    score = float(min(100.0, 100.0 * (1.0 - np.exp(-1.2 * index))))

    return {
        "overall_risk_score": round(score, 1),
        "overall_level": _score_level(score),
        "risk_index": round(index, 4),
        "stress_index": round(stress_index, 4),
        "peel_index": round(peel_index, 4),
        "mean_risk_area_weighted": round(mean_w, 4),
        "p90_risk_area_weighted": round(p90_w, 4),
        "max_risk": round(float(risks.max()), 4),
        "worst_layer": {"z": worst.z, "risk": worst.risk, "kt": worst.kt,
                        "level": worst.level},
        "max_delta_t_k": round(float(dts.max()), 2),
        "mean_delta_t_k": round(float(dts.mean()), 2),
        "max_section_area_mm2": round(float(areas.max()), 2),
        "peak_area_z": peak.z,
        "mean_section_area_mm2": round(float(areas.mean()), 2),
        "max_area_change": round(float(changes.max()), 4),
        "abrupt_layers": len(abrupt),
        "abrupt_z": [l.z for l in abrupt[:15]],
        "max_islands": int(max(l.islands for l in layers)),
        "layers_analyzed": len(layers),
        "layers_significant": len(core),
        "hotspot_count": len(hotspots),
        "material_uts_mpa": mat.uts,
    }


def _recommend(summary, layers, mat: Material, st: PrintSettings) -> List[str]:
    rec: List[str] = []
    s = summary

    if s["overall_level"] in ("高", "極高"):
        rec.append(
            f"整體風險為「{s['overall_level']}」（分數 {s['overall_risk_score']}）："
            "建議先改幾何再調參數，單靠曝光參數難以救回。")

    if s["max_area_change"] > 0.35:
        zs = ", ".join(f"{z:.2f}" for z in s["abrupt_z"][:5])
        rec.append(
            f"偵測到 {s['abrupt_layers']} 層截面突變（最大 {s['max_area_change']*100:.0f}%，"
            f"z ≈ {zs} mm）。突變層同時是剝離力尖峰與熱收縮約束尖峰，"
            "請在此高度加密支撐，或把幾何改成逐層漸進（加拔模角 / 大圓角過渡）。")

    if s["max_delta_t_k"] > 12:
        rec.append(
            f"厚實區的固化溫升估計可達 {s['max_delta_t_k']:.1f} K，"
            "溫度分布不均是熱應力主因。建議把實心區挖空成 2~3 mm 殼厚 + 網格加強筋，"
            "並適度降低曝光時間或改用低放熱樹脂。")

    thick = np.array([l.thickness for l in layers])
    if thick.max() > 6 * max(thick.mean(), 1e-6):
        rec.append("模型同時存在厚實塊體與薄壁：兩者收縮速率差會在交界產生剪應力，"
                   "請在交界處加圓角（R ≥ 1 mm）並考慮分件列印後黏合。")

    if s["max_section_area_mm2"] > 2500:
        rec.append(
            f"單層最大截面 {s['max_section_area_mm2']:.0f} mm²，剝離吸力偏高。"
            "建議傾斜擺放 30°~45°、避免任何面平行 FEP，並延長抬升時間。")

    if s["max_islands"] > 4:
        rec.append(f"最多有 {s['max_islands']} 個獨立島嶼，孤島起始層必須有支撐，"
                   "否則會在液面漂移並造成錯層應力。")

    rec.append("尖銳內凹角是熱收縮應力的放大器：把 Kt 高的角改成 R ≥ 0.5 mm 圓角，"
               "通常可讓局部應力下降 30~50%（Kt ≈ 1 + 0.5·√(t/R)，加大半徑最有效）。")

    rec.append("列印後不要立刻強光長時間二次固化：先在 30~40 °C 溫水中緩慢後固化，"
               "讓黏彈鬆弛帶走一部分殘留應力，可明顯降低開裂機率。")

    if mat.cure_shrinkage > 0.012:
        rec.append(f"目前材料線收縮率 {mat.cure_shrinkage*100:.1f}% 偏高，"
                   "若尺寸精度重要，建議改用低收縮 / 填料強化樹脂。")
    return rec
