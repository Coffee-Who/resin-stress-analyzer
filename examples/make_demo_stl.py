"""輸出示範模型。

用法：
    python examples/make_demo_stl.py [輸出路徑] [--fillet 2]
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from resin_stress.demo import build  # noqa: E402


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    fillet = 0.0
    if "--fillet" in sys.argv:
        fillet = float(sys.argv[sys.argv.index("--fillet") + 1])
        args = [a for a in args if a != str(fillet)]

    out = Path(args[0] if args else "examples/demo_part.stl")
    out.parent.mkdir(parents=True, exist_ok=True)
    mesh = build(fillet)
    mesh.export(out)
    print(f"已輸出 {out}  尺寸 {np.round(mesh.extents, 2)} mm"
          + (f"  (內凹角導 R{fillet})" if fillet else ""))


if __name__ == "__main__":
    main()
