"""Recalculate an .xlsx with LibreOffice headless (openpyxl saves formulas without values)."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from openpyxl import load_workbook


def find_soffice() -> str | None:
    for cand in [shutil.which("soffice"), shutil.which("libreoffice"),
                 r"C:\Program Files\LibreOffice\program\soffice.exe",
                 r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
                 "/Applications/LibreOffice.app/Contents/MacOS/soffice"]:
        if cand and Path(cand).exists():
            return cand
    return None


def recalc(src: Path, outdir: Path | None = None):
    """Return the recalculated workbook (values only) for `src`."""
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError("LibreOffice not installed")
    outdir = Path(outdir or tempfile.mkdtemp())
    profile = Path(tempfile.mkdtemp())
    subprocess.run([soffice, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--calc",
                    "--convert-to", "xlsx", "--outdir", str(outdir), str(src)],
                   check=True, capture_output=True, timeout=1800)
    return load_workbook(outdir / Path(src).name, data_only=True)
