"""Deploy must ship app/data without deleting the live database or logs."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is not installed")
def test_deploy_sync_preserves_runtime_data_but_updates_product_catalog(tmp_path):
    script = (Path(__file__).resolve().parents[2] / "deploy/deploy.sh").read_text()
    block = script.split("rsync -a --delete \\\n", 1)[1].split('echo "[deploy] 后端已同步', 1)[0]
    exclusions = re.findall(r"--exclude='([^']+)'", block)
    source, live = tmp_path / "source", tmp_path / "live"
    for root in (source, live):
        (root / "app/data").mkdir(parents=True)
    for directory in ("data", "logs", ".venv"):
        (live / directory).mkdir()
        (live / directory / "keep").write_text("runtime data")
    (source / "app/data/apple_catalog.json").write_text('{"products":["Duo"]}')
    (live / "app/data/apple_catalog.json").write_text('{"products":[]}')
    (live / "app/obsolete.py").write_text("stale")
    subprocess.run(
        [
            "rsync",
            "-a",
            "--delete",
            *[f"--exclude={pattern}" for pattern in exclusions],
            str(source) + "/",
            str(live) + "/",
        ],
        check=True,
        capture_output=True,
    )
    assert (live / "app/data/apple_catalog.json").read_text() == '{"products":["Duo"]}'
    assert not (live / "app/obsolete.py").exists()
    assert all((live / d / "keep").read_text() == "runtime data" for d in ("data", "logs", ".venv"))
