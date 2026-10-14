"""The Colab notebook embeds the training code; it must not drift from it."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _builder():  # training/ isn't a package on the plant-cam path; load the script directly
    spec = importlib.util.spec_from_file_location("make_notebook", ROOT / "training" / "colab" / "make_notebook.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_notebook_embeds_current_training_code():
    mb = _builder()
    nb = json.loads(mb.NOTEBOOK.read_text(encoding="utf-8"))
    assert mb.embedded_bundle(nb) == mb.bundle(), (
        "training code or profile changed: run `python colab/make_notebook.py` in edge/plant-cam/training"
    )


def test_notebook_cells_are_valid_python():
    nb = json.loads(_builder().NOTEBOOK.read_text(encoding="utf-8"))
    for i, cell in enumerate(c for c in nb["cells"] if c["cell_type"] == "code"):
        compile("".join(cell["source"]), f"cell {i}", "exec")
