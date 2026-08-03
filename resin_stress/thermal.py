"""固化放熱溫升與熱應力 / 收縮應力的一階模型。

對應 CHITUBOX 文章的物理鏈：
    紫外光激發 -> 交聯放熱 -> 局部溫升不均 -> 內部變形約束 -> 應力集中

一、放熱溫升
------------
單位體積放熱      q_v  = dH * DoC * rho                [J/mm^3]
成型面熱通量      q''  = q_v * h / t_layer             [W/mm^2]
單層熱擴散深度    L_p  = 2 * sqrt(a * t_layer)         [mm]
形狀散熱因子      S    = 1 + L_p / t_eff               [-]
                  厚實區 t_eff >> L_p -> S ~ 1（散不掉，溫升高）
                  薄壁   t_eff << L_p -> S 大（側面散熱，溫升低）
準穩態溫升        dT   = q'' / (h_conv * S)，並以絕熱溫升為上限

h_conv 為固化件對樹脂槽的等效表面散熱係數，液體自然對流量級
約 60~150 W/(m^2*K)，預設取 100。

二、應力
--------
受約束熱應力      sigma_th = E * alpha * dT / (1 - nu)
固化收縮應力      sigma_sh = E * eps_lin * (1 - relaxation) / (1 - nu)

注意：均勻升溫且不受約束並不產生應力，真正產生應力的是「溫度分布不均
＋變形受到約束」。因此上式再乘上由幾何推得的約束程度 gradient_factor。

以上皆為一階估算，供相對比較與排序使用，不等同有限元素分析結果。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .materials import Material

DEFAULT_H_CONV_W_M2K = 100.0  # 樹脂槽液體自然對流等效散熱係數


@dataclass
class ThermalState:
    delta_t: float          # K，該層準穩態局部溫升
    shape_factor: float     # 散熱形狀因子 S
    thermal_stress: float   # MPa，受約束熱應力
    shrink_stress: float    # MPa，固化收縮應力
    driving_stress: float   # MPa，名義驅動應力（熱 + 收縮）
    constraint: float       # 0~1，約束程度


def exotherm_delta_t(material: Material, effective_thickness: float,
                     layer_height: float, layer_time: float,
                     h_conv_w_m2k: float = DEFAULT_H_CONV_W_M2K) -> tuple:
    """回傳 (溫升 K, 形狀因子)。"""
    a = material.diffusivity_mm2_s
    t_eff = max(effective_thickness, layer_height)

    q_flux = material.volumetric_heat * layer_height / max(layer_time, 1e-3)
    penetration = 2.0 * math.sqrt(a * max(layer_time, 1e-3))
    shape = 1.0 + penetration / t_eff
    shape = min(shape, 30.0)

    h_conv_mm = h_conv_w_m2k * 1e-6  # W/(mm^2*K)
    dT = q_flux / (h_conv_mm * shape)
    return min(dT, material.adiabatic_delta_t), shape


def layer_thermal_state(material: Material, effective_thickness: float,
                        layer_height: float, layer_time: float,
                        constraint: float = 1.0,
                        h_conv_w_m2k: float = DEFAULT_H_CONV_W_M2K
                        ) -> ThermalState:
    """計算單層的溫升與名義驅動應力。

    constraint: 約束程度 0~1，由幾何不均勻度推得（見 constraint_factor）。
    """
    dT, shape = exotherm_delta_t(material, effective_thickness,
                                 layer_height, layer_time, h_conv_w_m2k)
    c = max(0.0, min(constraint, 1.0))

    sigma_th = material.E * material.alpha * dT / (1.0 - material.nu) * c
    sigma_sh = (material.E * material.cure_shrinkage
                * (1.0 - material.relaxation) / (1.0 - material.nu)) * c

    return ThermalState(
        delta_t=float(dT),
        shape_factor=float(shape),
        thermal_stress=float(sigma_th),
        shrink_stress=float(sigma_sh),
        driving_stress=float(sigma_th + sigma_sh),
        constraint=float(c),
    )


def penetration_depth(material: Material, layer_time: float) -> float:
    """單層週期內的熱擴散深度 L_p = 2*sqrt(a*t)，約 1~2 mm。"""
    return 2.0 * math.sqrt(material.diffusivity_mm2_s * max(layer_time, 1e-3))


def constraint_factor(area_change_ratio: float, thickness: float,
                      penetration: float) -> float:
    """推估「變形受到約束」的程度（0~1）。

    對應文章列出的三種約束來源：

    - 外部變形約束：貼平台、支撐 -> 基底值 0.20
    - 層間變形約束：截面突變時新層被舊層卡住 -> area_change 項
    - 內部變形約束：厚實塊體內外溫差大，冷的芯部約束熱的表層 -> bulk 項
      厚度遠大於熱擴散深度 L_p 時內部才會形成明顯梯度。
    """
    step = min(1.0, abs(area_change_ratio) * 4.0)
    ratio = thickness / max(penetration, 1e-6)
    bulk = min(1.0, max(0.0, (ratio - 1.0) / 5.0))
    return float(min(1.0, 0.20 + 0.45 * step + 0.45 * bulk))
