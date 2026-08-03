"""樹脂材料熱物性資料庫。

所有數值皆為「典型量級」的公開文獻值，用於一階估算與相對比較；
若要做定量預測，請以自家樹脂的 TDS / DSC 量測值覆蓋。

單位約定
--------
alpha            : 1/K            線膨脹係數
E                : MPa            固化後楊氏模數（室溫）
nu               : -              蒲松比
uts              : MPa            抗拉強度
cure_shrinkage   : -              固化「線」收縮率（體積收縮率約為 3 倍）
enthalpy         : J/g            單位質量聚合放熱（丙烯酸酯類約 250~450）
cp               : J/(g*K)        比熱
density          : g/cm^3         密度
conductivity     : W/(m*K)        熱傳導係數
doc              : -              單層曝光的轉化率（degree of conversion）
relaxation       : -              固化初期黏彈鬆弛比例（0=完全不鬆弛）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict


@dataclass(frozen=True)
class Material:
    name: str
    label: str
    alpha: float
    E: float
    nu: float
    uts: float
    cure_shrinkage: float
    enthalpy: float
    cp: float
    density: float
    conductivity: float
    doc: float = 0.6
    relaxation: float = 0.5

    @property
    def diffusivity_mm2_s(self) -> float:
        """熱擴散係數 a = k / (rho * cp)，單位 mm^2/s。

        k   [W/(m*K)]   -> /1000 得 [J/(s*mm*K)]
        rho [g/cm^3]    -> /1000 得 [g/mm^3]
        典型光敏樹脂約 0.09 mm^2/s。
        """
        k_mm = self.conductivity / 1000.0        # J/(s*mm*K)
        rho_mm = self.density / 1000.0           # g/mm^3
        return k_mm / (rho_mm * self.cp)         # mm^2/s

    @property
    def volumetric_heat(self) -> float:
        """單位體積聚合放熱量 J/mm^3（已乘上轉化率）。"""
        return self.enthalpy * self.doc * (self.density / 1000.0)

    @property
    def adiabatic_delta_t(self) -> float:
        """絕熱條件下（完全不散熱）的理論最大溫升 (K)，作為上限。"""
        return self.enthalpy * self.doc / self.cp

    def to_dict(self) -> dict:
        d = asdict(self)
        d["diffusivity_mm2_s"] = round(self.diffusivity_mm2_s, 5)
        d["adiabatic_delta_t"] = round(self.adiabatic_delta_t, 2)
        return d


_BUILTIN: Dict[str, Material] = {
    "standard": Material(
        name="standard", label="標準模型樹脂",
        alpha=95e-6, E=2100, nu=0.35, uts=45,
        cure_shrinkage=0.011, enthalpy=350, cp=1.75,
        density=1.15, conductivity=0.19, doc=0.60, relaxation=0.50,
    ),
    "tough": Material(
        name="tough", label="韌性 / ABS-like 樹脂",
        alpha=120e-6, E=1600, nu=0.38, uts=42,
        cure_shrinkage=0.008, enthalpy=290, cp=1.85,
        density=1.12, conductivity=0.20, doc=0.55, relaxation=0.65,
    ),
    "rigid": Material(
        name="rigid", label="高剛性 / 填料強化樹脂",
        alpha=60e-6, E=4200, nu=0.33, uts=60,
        cure_shrinkage=0.006, enthalpy=260, cp=1.30,
        density=1.45, conductivity=0.32, doc=0.62, relaxation=0.30,
    ),
    "heat_resistant": Material(
        name="heat_resistant", label="耐高溫樹脂",
        alpha=55e-6, E=3500, nu=0.34, uts=55,
        cure_shrinkage=0.009, enthalpy=380, cp=1.45,
        density=1.30, conductivity=0.28, doc=0.65, relaxation=0.25,
    ),
    "dental": Material(
        name="dental", label="牙科模型樹脂",
        alpha=80e-6, E=2600, nu=0.35, uts=50,
        cure_shrinkage=0.007, enthalpy=300, cp=1.60,
        density=1.20, conductivity=0.22, doc=0.62, relaxation=0.40,
    ),
    "flexible": Material(
        name="flexible", label="彈性 / TPU-like 樹脂",
        alpha=180e-6, E=180, nu=0.45, uts=15,
        cure_shrinkage=0.014, enthalpy=320, cp=1.90,
        density=1.10, conductivity=0.18, doc=0.50, relaxation=0.80,
    ),
    "castable": Material(
        name="castable", label="鑄造 / 蠟性樹脂",
        alpha=150e-6, E=900, nu=0.40, uts=20,
        cure_shrinkage=0.016, enthalpy=400, cp=1.95,
        density=1.08, conductivity=0.17, doc=0.55, relaxation=0.60,
    ),
}


def list_materials() -> Dict[str, Material]:
    return dict(_BUILTIN)


def get_material(name: str, custom_file: str | Path | None = None) -> Material:
    """取得材料。custom_file 為 JSON，可覆寫或新增材料。"""
    table = dict(_BUILTIN)
    if custom_file:
        data = json.loads(Path(custom_file).read_text(encoding="utf-8"))
        if isinstance(data, dict) and "name" in data:
            data = {data["name"]: data}
        for key, val in data.items():
            base = table.get(key)
            merged = {**(asdict(base) if base else {}), **val}
            merged.setdefault("name", key)
            merged.setdefault("label", key)
            table[key] = Material(**merged)

    if name not in table:
        raise KeyError(
            f"未知材料 '{name}'，可用：{', '.join(sorted(table))}"
        )
    return table[name]
