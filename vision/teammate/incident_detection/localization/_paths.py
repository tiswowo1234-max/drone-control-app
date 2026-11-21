"""Name the folders this one reads from.

[drone-control-app 수정 2026-11-21] 원본은 옆 폴더 LLM_detects를 sys.path에 넣고, 없으면 ImportError를 냈다.
LLM 판별은 복사하지 않았으므로(API 키·openai 의존) 그 검사를 뺐다. image_io.py는 LLM_detects에서 이 폴더로 복사했다.
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
LLM_DETECTS = HERE.parent / "LLM_detects"  # 복사하지 않음 (pipeline.py --detect, map_layout.py는 동작하지 않는다)
DATA = REPO_ROOT / "data"
