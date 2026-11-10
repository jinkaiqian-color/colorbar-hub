#!/usr/bin/env python3
"""
Assemble the static website into ./_site

    python tools/build_site.py
    python -m http.server -d _site 8000      # then open http://localhost:8000

The site is the files in site/ plus the engine (colorbar_core.py) and the sample
sheet, so the engine has a single source of truth in the repository root.
GitHub Actions runs this same script before publishing.
"""
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "_site")

if os.path.isdir(OUT):
    shutil.rmtree(OUT)
shutil.copytree(os.path.join(ROOT, "site"), OUT)
shutil.copy(os.path.join(ROOT, "colorbar_core.py"), OUT)
shutil.copy(os.path.join(ROOT, "tests", "sample", "sample_bar.pdf"), OUT)
open(os.path.join(OUT, ".nojekyll"), "w").close()
print("Built", OUT)
