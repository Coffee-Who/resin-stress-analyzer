"""截面幾何缺口（凹角）偵測與應力集中係數 Kt 估算。

理論依據
--------
缺口／肩部圓角的應力集中可寫成 Peterson 型式：

    Kt = 1 + C * f(theta) * sqrt(t / r)

    r     : 凹角根部的圓角半徑（越小越尖）
    t     : 特徵尺寸（相鄰臂長，代表缺口的「深度」）
    theta : 材料側夾角，越尖越嚴重， f = (180 - theta) / 90
    C     : 經驗係數，取 0.5（肩部圓角試驗值約 0.4~0.6）

這正是 CHITUBOX 文章「不同溫度區域的收縮差異導致應力集中」的幾何放大器：
熱應變 ε = αΔT 提供驅動力，尖銳凹角決定它集中在哪一點。

半徑估算
--------
單純用三點外接圓（Menger 曲率）會把「長臂尖角」誤判成大半徑，因此改用
「轉角串」的作法：把連續同向的凹轉頂點視為同一個轉角，
    r ≈ 弧長 / 總轉角(rad)
真正的尖角只有單一頂點、弧長為 0，取列印解析度作為半徑下限；
有導圓角的轉角會被切成多個小轉折，就能還原出接近實際的 R 值。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

import numpy as np
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

KT_COEFF = 0.5
KT_CAP = 8.0
DEPTH_CAP_MM = 10.0


@dataclass
class NotchFeature:
    x: float
    y: float
    z: float
    radius: float        # mm，凹角根部圓角半徑
    depth: float         # mm，等效缺口深度（相鄰臂長）
    angle_deg: float     # 材料側夾角，越小越尖
    kt: float            # 應力集中係數


def kt_from_geometry(depth: float, radius: float, angle_deg: float,
                     coeff: float = KT_COEFF, cap: float = KT_CAP) -> float:
    """Peterson 型式的應力集中係數。"""
    r = max(radius, 1e-6)
    d = max(min(depth, DEPTH_CAP_MM), 0.0)
    sharpness = max(0.0, (180.0 - angle_deg) / 90.0)
    kt = 1.0 + coeff * sharpness * float(np.sqrt(d / r))
    return float(min(kt, cap))


def polygon_notches(poly: Polygon, z: float, min_radius: float,
                    angle_threshold_deg: float = 160.0,
                    simplify_tol: float | None = None) -> List[NotchFeature]:
    """找出單一截面多邊形上所有「材料側凹角」並計算 Kt。"""
    if simplify_tol is None:
        simplify_tol = min_radius * 0.5

    poly = orient(poly, sign=1.0)  # 外環 CCW、內孔 CW
    simple = poly.simplify(simplify_tol, preserve_topology=True)
    if simple.is_empty:
        return []

    features: List[NotchFeature] = []
    for ring in [simple.exterior, *list(simple.interiors)]:
        features.extend(_ring_notches(ring, z, min_radius,
                                      angle_threshold_deg))
    return features


def _ring_notches(ring, z: float, min_radius: float,
                  angle_threshold_deg: float) -> List[NotchFeature]:
    pts = np.asarray(ring.coords)[:-1]
    n = len(pts)
    if n < 4:
        return []

    turns = np.zeros(n)          # 有號轉角（rad），負值 = 材料側凹角
    edge_len = np.zeros(n)       # 頂點 i 到 i+1 的邊長
    for i in range(n):
        a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
        v1, v2 = b - a, c - b
        l1, l2 = np.linalg.norm(v1), np.linalg.norm(v2)
        edge_len[i] = l2
        if l1 < 1e-9 or l2 < 1e-9:
            continue
        cross = v1[0] * v2[1] - v1[1] * v2[0]
        dot = float(np.dot(v1, v2))
        turns[i] = np.arctan2(cross, dot)

    concave = turns < -1e-6
    if not concave.any():
        return []

    features: List[NotchFeature] = []
    for run in _split_runs(_runs(concave), turns):
        total_turn = float(-turns[run].sum())
        if total_turn < 1e-6:
            continue
        interior_angle = 180.0 - np.degrees(total_turn)
        if interior_angle > angle_threshold_deg:
            continue  # 幾乎是直線

        # 轉角串內部的弧長（單一尖角時為 0）
        arc = float(sum(edge_len[run[k]] for k in range(len(run) - 1)))
        radius = max(arc / total_turn, min_radius)

        # 缺口深度：進入與離開這個轉角的兩條臂長取小者
        arm_in = float(edge_len[run[0] - 1])
        arm_out = float(edge_len[run[-1]])
        depth = min(arm_in, arm_out)

        mid = pts[run[len(run) // 2]]
        features.append(NotchFeature(
            x=float(mid[0]), y=float(mid[1]), z=float(z),
            radius=float(radius), depth=float(depth),
            angle_deg=float(max(interior_angle, 0.0)),
            kt=kt_from_geometry(depth, radius, interior_angle),
        ))
    return features


def _split_runs(runs: List[List[int]], turns: np.ndarray,
                max_turn: float = np.pi / 2) -> List[List[int]]:
    """把累積轉角超過 90 度的長串切開。

    沒有這一步的話，一個離散化的圓孔會被當成單一「累積 360 度」的
    巨大凹角；切開後每段最多 90 度，才能正確還原局部曲率半徑。
    """
    out: List[List[int]] = []
    for run in runs:
        acc, current = 0.0, []
        for i in run:
            t = abs(float(turns[i]))
            if current and acc + t > max_turn:
                out.append(current)
                acc, current = 0.0, []
            current.append(i)
            acc += t
        if current:
            out.append(current)
    return out


def _runs(mask: np.ndarray) -> List[List[int]]:
    """把布林環狀陣列切成連續 True 的索引串（處理跨頭尾的情形）。"""
    n = len(mask)
    idx = np.where(mask)[0]
    if len(idx) == 0:
        return []
    if mask.all():
        return [list(range(n))]

    start = int(idx[0])
    while mask[start - 1]:
        start -= 1
    start %= n

    runs, current = [], []
    for k in range(n):
        i = (start + k) % n
        if mask[i]:
            current.append(i)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def layer_notches(polygons, z: float, min_radius: float,
                  keep_top: int = 8, **kwargs) -> List[NotchFeature]:
    """整層的凹角，僅保留 Kt 最大的前 keep_top 個以控制資料量。"""
    feats: List[NotchFeature] = []
    for poly in polygons:
        feats.extend(polygon_notches(poly, z, min_radius, **kwargs))
    feats.sort(key=lambda f: f.kt, reverse=True)
    return feats[:keep_top]
