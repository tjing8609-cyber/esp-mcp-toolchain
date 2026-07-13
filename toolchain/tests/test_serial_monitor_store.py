from __future__ import annotations

import hashlib
import json

from esp_mcp_toolchain.backends.serial_monitor_store import recover_serial_runs


def test_recover_serial_run_finalizes_part_and_indexes_chunk(tmp_path):
    log_root = tmp_path / "logs"
    run_dir = log_root / "serial" / "monitor_stale"
    run_dir.mkdir(parents=True)
    payload = b"unfinished-raw-bytes"
    (run_dir / "chunk-000001.bin.part").write_bytes(payload)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "monitor_stale",
                "project_id": "project-test",
                "state": "RUNNING",
                "chunks": [],
            }
        ),
        encoding="utf-8",
    )

    recovered = recover_serial_runs(log_root)

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    final_chunk = run_dir / "chunk-000001.bin"
    assert recovered[0]["run_id"] == "monitor_stale"
    assert final_chunk.read_bytes() == payload
    assert not (run_dir / "chunk-000001.bin.part").exists()
    assert manifest["state"] == "FAILED"
    assert manifest["last_error"]["error_kind"] == "stale_monitor_recovered"
    assert manifest["chunks"] == [
        {
            "chunk_id": 1,
            "path": str(final_chunk),
            "byte_length": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "recovered": True,
        }
    ]


def test_recover_serial_run_skips_active_run(tmp_path):
    log_root = tmp_path / "logs"
    run_dir = log_root / "serial" / "monitor_active"
    run_dir.mkdir(parents=True)
    (run_dir / "chunk-000001.bin.part").write_bytes(b"active")
    (run_dir / "manifest.json").write_text(
        json.dumps({"run_id": "monitor_active", "state": "RUNNING"}),
        encoding="utf-8",
    )

    recovered = recover_serial_runs(log_root, skip_run_ids={"monitor_active"})

    assert recovered == []
    assert (run_dir / "chunk-000001.bin.part").exists()
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["state"] == "RUNNING"
