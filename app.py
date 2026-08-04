"""resin-stress-analyzer 的網頁介面。

本機執行：
    pip install -r requirements.txt
    streamlit run app.py

瀏覽器會自動打開 http://localhost:8501
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from resin_stress import PrintSettings
from resin_stress.bundle import analyze_bytes
from resin_stress.demo import stl_bytes
from resin_stress.materials import list_materials

MAX_FACES_WARN = 300_000
LEVEL_COLOR = {"低": "#3aa66e", "中": "#e0b21e", "高": "#e07b1e", "極高": "#c9302c"}

st.set_page_config(page_title="光固化列印應力分析", page_icon="🧊",
                   layout="wide")


# --------------------------------------------------------------- 分析（含快取）
@st.cache_data(show_spinner=False, max_entries=8)
def run_analysis(file_bytes: bytes, name: str, material: str,
                 layer_height: float, pixel_size: float, exposure: float,
                 lift_time: float, tilt: float, spin: float, scale: float,
                 max_layers: int):
    """同樣的檔案與參數不會重算。"""
    settings = PrintSettings(
        layer_height=layer_height, pixel_size=pixel_size,
        exposure_time=exposure, lift_time=lift_time,
        tilt_deg=tilt, spin_deg=spin, max_layers=max_layers,
    )
    b = analyze_bytes(file_bytes, name, material=material,
                      settings=settings, scale=scale)
    return b.result, b.files, b.viewer_html


# --------------------------------------------------------------- 側邊欄
with st.sidebar:
    st.title("🧊 應力分析")
    st.caption("光固化 3D 列印熱應力與應力集中篩檢")

    uploaded = st.file_uploader(
        "上傳模型", type=["stl", "obj", "ply", "3mf", "off"],
        help="單位視為 mm。若模型是以 cm 或 inch 建立，請調整下方的縮放倍率。")

    materials = list_materials()
    mat_key = st.selectbox(
        "樹脂材料", list(materials),
        format_func=lambda k: materials[k].label,
        help="建議用自家樹脂的 TDS / DSC 數據覆蓋內建參數")

    m = materials[mat_key]
    st.caption(f"E {m.E} MPa　·　UTS {m.uts} MPa　·　"
               f"線收縮 {m.cure_shrinkage * 100:.2f}%")

    st.divider()
    st.subheader("列印參數")
    c1, c2 = st.columns(2)
    layer_height = c1.number_input("層厚 mm", 0.01, 0.20, 0.05, 0.01,
                                   format="%.3f")
    pixel_size = c2.number_input("像素 mm", 0.010, 0.150, 0.035, 0.005,
                                 format="%.3f",
                                 help="決定可成形的最小圓角半徑")
    exposure = c1.number_input("曝光 s", 0.5, 30.0, 2.5, 0.5)
    lift_time = c2.number_input("抬升 s", 0.5, 30.0, 4.0, 0.5)

    st.subheader("擺放與縮放")
    c3, c4 = st.columns(2)
    tilt = c3.slider("傾斜角°", 0, 90, 0, 5, help="繞 X 軸，模擬斜放")
    spin = c4.slider("旋轉角°", 0, 360, 0, 15, help="繞 Z 軸")
    scale = st.number_input("縮放倍率", 0.01, 100.0, 1.0, 0.1)

    with st.expander("進階"):
        max_layers = st.slider("取樣層數上限", 100, 1200, 400, 50,
                               help="層數越多越精細，但計算時間也越長")

    go = st.button("開始分析", type="primary", width='stretch',
                   disabled=uploaded is None)
    demo = st.button("沒有模型？用示範件試跑", width='stretch')


# --------------------------------------------------------------- 主畫面
if demo:
    st.session_state["demo_file"] = ("demo_part.stl", stl_bytes())

source = None
if uploaded is not None:
    source = (uploaded.name, uploaded.getvalue())
elif "demo_file" in st.session_state:
    source = st.session_state["demo_file"]

if source is None:
    st.title("光固化列印熱應力／應力集中分析")
    st.markdown(
        "從左側上傳一個 **STL** 開始。程式會逐層切片，估算固化放熱造成的溫升與收縮應力，"
        "找出幾何上會把應力放大的**尖銳內凹角**與**截面突變層**，"
        "並把結果直接標在 3D 模型上。")
    a, b, c = st.columns(3)
    a.info("**找出裂在哪**\n\n尖角的應力集中係數 Kt 可達 8 倍，"
           "報告會給出 XY 座標與高度範圍，直接回 CAD 導圓角。")
    b.info("**比較不同版本**\n\n改圓角、換擺放角度、換材料後重算，"
           "用分數比較哪一版比較安全。")
    c.warning("**這不是 FEA**\n\n一階估算與排序工具。"
              "MPa 數字請當相對比較用，不要拿去做結構驗證。")
    st.stop()

if not (go or demo) and "result" not in st.session_state:
    st.info("參數設定好了就按左側的「開始分析」。")
    st.stop()

if go or demo:
    with st.spinner("切片與分析中…"):
        try:
            st.session_state["result"] = run_analysis(
                source[1], source[0], mat_key, layer_height, pixel_size,
                exposure, lift_time, float(tilt), float(spin), scale,
                max_layers)
        except Exception as exc:
            st.error(f"分析失敗：{exc}")
            st.stop()

result, files, viewer = st.session_state["result"]
s = result.summary
stem = Path(source[0]).stem

if result.mesh["faces"] > MAX_FACES_WARN:
    st.warning(f"模型有 {result.mesh['faces']:,} 個三角面，"
               "3D 檢視可能會卡。建議先減面再上傳。")
if not result.mesh["watertight"]:
    st.warning("網格不封閉，切片結果可能不完整——建議先修復模型。")

# --- 總評 ---
lv = s["overall_level"]
st.markdown(
    f"### {stem}　<span style='color:{LEVEL_COLOR[lv]}'>{s['overall_risk_score']} / 100"
    f"　{lv}</span>", unsafe_allow_html=True)
st.progress(min(s["overall_risk_score"] / 100, 1.0))

k = st.columns(5)
k[0].metric("應力指數", f"{s['stress_index']:.2f}",
            help="熱應力 + 收縮應力經幾何放大後與材料強度的比值")
k[1].metric("剝離指數", f"{s['peel_index']:.2f}",
            help="單層最大截面帶來的吸附力與收縮體積")
k[2].metric("最大溫升", f"{s['max_delta_t_k']} K")
k[3].metric("最大截面", f"{s['max_section_area_mm2']:.0f} mm²")
k[4].metric("截面突變層", f"{s['abrupt_layers']} 層")

tab_3d, tab_chart, tab_hot, tab_rec, tab_data = st.tabs(
    ["🧊 3D 熱點", "📈 分析圖表", "🎯 熱點清單", "🛠 改善建議", "📋 逐層數據"])

with tab_3d:
    st.iframe(viewer, height=620)
    st.caption("拖曳旋轉 · 滾輪縮放 · 右鍵平移。紅色為高風險區。")
    st.image(files["views"], caption="四視角靜態圖")

with tab_chart:
    st.image(files["charts"])

with tab_hot:
    if result.hotspots:
        df = pd.DataFrame(result.hotspots)
        df = df.rename(columns={
            "x": "X", "y": "Y", "z_min": "起始 z", "z_max": "結束 z",
            "angle_deg": "夾角°", "radius_mm": "現有 R", "kt": "Kt",
            "local_stress_mpa": "局部應力 MPa", "risk": "風險比",
            "level": "等級", "layers": "涵蓋層數", "z": "代表 z",
        })
        st.dataframe(df.drop(columns=["代表 z"]), width='stretch',
                     hide_index=True)
        worst = result.hotspots[0]
        st.info(f"最該處理的是 **({worst['x']}, {worst['y']})** 這條稜線，"
                f"從 z = {worst['z_min']} 延伸到 {worst['z_max']} mm，"
                f"目前圓角只有 R{worst['radius_mm']}，Kt = {worst['kt']}。")
    else:
        st.success("沒有偵測到明顯的尖銳凹角。")

with tab_rec:
    for r in result.recommendations:
        st.markdown(f"- {r}")

with tab_data:
    df = pd.DataFrame([vars(l) for l in result.layers])
    st.line_chart(df.set_index("z")[["risk", "kt", "delta_t"]], height=260)
    st.dataframe(df, width='stretch', hide_index=True, height=320)

# --- 下載 ---
st.divider()
d = st.columns(6)
for col, (label, key, fname, mime) in zip(d, [
    ("報告 MD", "md", f"{stem}_report.md", "text/markdown"),
    ("逐層 CSV", "csv", f"{stem}_layers.csv", "text/csv"),
    ("完整 JSON", "json", f"{stem}_report.json", "application/json"),
    ("圖表 PNG", "charts", f"{stem}_charts.png", "image/png"),
    ("3D 圖 PNG", "views", f"{stem}_3d.png", "image/png"),
    ("彩色網格 PLY", "ply", f"{stem}_colored.ply", "application/octet-stream"),
]):
    col.download_button(label, files[key], fname, mime,
                        width='stretch')

st.download_button("互動 3D 檢視 HTML", viewer.encode("utf-8"),
                   f"{stem}_viewer.html", "text/html")
st.caption("本工具為一階估算與排序用途，不等同有限元素分析結果。")
