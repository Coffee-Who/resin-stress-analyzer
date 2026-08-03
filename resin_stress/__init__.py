"""resin-stress-analyzer

光固化（SLA / LCD / DLP）3D 列印的熱應力與應力集中篩檢工具。
"""

from .analyzer import AnalysisResult, LayerResult, PrintSettings, analyze
from .materials import Material, get_material, list_materials

__version__ = "0.1.0"
__all__ = [
    "analyze", "AnalysisResult", "LayerResult", "PrintSettings",
    "Material", "get_material", "list_materials", "__version__",
]
