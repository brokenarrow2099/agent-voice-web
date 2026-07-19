from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/benchmark-speaker-verifier.py"
SPEC = importlib.util.spec_from_file_location("speaker_benchmark_script", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_benchmark_requires_three_enrollment_files_and_reports_json():
    parser = MODULE.build_parser()
    args = parser.parse_args(
        [
            "--enroll",
            "one.wav",
            "two.wav",
            "three.wav",
            "--probe",
            "probe.wav",
            "--url",
            "http://127.0.0.1:8767",
        ]
    )

    assert len(args.enroll) == 3
    assert args.probe.name == "probe.wav"
    assert args.url == "http://127.0.0.1:8767"
