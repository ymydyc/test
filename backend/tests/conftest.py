"""pytest 共享设施：将 backend 加入 sys.path 以便 `import app.*`。"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))