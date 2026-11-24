"""
scripts/build_codebase_guide.py
Generates docs/CODEBASE_GUIDE.md and docs/CODEBASE_GUIDE.html
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"
DOCS_DIR.mkdir(exist_ok=True)

print("Starting guide generation...")
