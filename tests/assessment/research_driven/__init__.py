"""research_driven 包测试初始化。"""

from pathlib import Path
import sys

# 确保 tests/assessment/research_driven 可被当作子包导入 fixtures
sys.path.insert(0, str(Path(__file__).resolve().parent))
