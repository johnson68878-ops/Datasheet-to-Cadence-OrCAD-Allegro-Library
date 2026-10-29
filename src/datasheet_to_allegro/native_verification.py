"""Exercise generation in a clean local Allegro database, without a host BRD."""
from pathlib import Path
import json
import subprocess
import argparse
import hashlib
import sys

from . import __version__

from .models import FootprintSpec
from .geometry import build_geometry
from .allegro import generate_geometry_skill
from .actual_verification import (
    write_actual_export_skill, write_actual_replay, parse_actual_export,
    compare_spec_to_actual, write_actual_reports,
)


def verify_footprint(source: Path, target: Path, executable: Path, required_version: str | None = None, *, gate_mode: str = "release") -> dict:
    """Verify in a newly-created output directory; never reuse an existing DRA."""
    if gate_mode not in {"release","review"}:
        raise ValueError("gate_mode must be release or review")
    source, target, executable = source.resolve(), target.resolve(), executable.resolve()
    target.mkdir(parents=True, exist_ok=False)
    from .cadence_compatibility import executable_version, target_version_error
    actual_version = executable_version(executable)
    mismatch = target_version_error(actual_version, required_version)
    if mismatch:
        raise ValueError(mismatch)
    if not str(target).isascii():
        raise ValueError("Cadence output path must be ASCII; use a separate output directory.")
    selected_version = required_version or actual_version
    spec = FootprintSpec.model_validate_json(source.read_text(encoding="utf-8"))
    geometry = build_geometry(spec)
    (target / "footprint_spec.json").write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    if spec.document_scan.get("pin_reconciliation"):
        from .pin_reconciliation import write_pin_reconciliation
        write_pin_reconciliation(spec.document_scan["pin_reconciliation"], target, geometry, spec)
    skill = generate_geometry_skill(spec, geometry, target / "generate.il",
                                   approved_by="LOCAL_REGRESSION", gate_mode=gate_mode)
    launch = json.loads(skill.with_suffix(".launch.json").read_text())
    mismatch = target_version_error(selected_version, launch.get("target_version"))
    if mismatch:
        raise ValueError(mismatch)
    dra = target / launch["dra_name"]
    replay = target / "generate.scr"
    replay.write_text(f'skill load("{skill.resolve().as_posix()}")\nexit\nfillin no\n', encoding="ascii")
    args = [str(executable), "-nograph"]
    if launch["mode"] == "symbol_package":
        args += ["-symbol", "package", str(dra.resolve())]
    args += ["-s", str(replay.resolve())]
    # Cadence's crash-report helper can inherit stdout pipes after the editor
    # exits. Use a file so a helper cannot keep communicate() blocked forever.
    with (target / "generation_session.log").open("wb") as log_stream:
        run = subprocess.run(args, cwd=target, stdout=log_stream, stderr=subprocess.STDOUT, timeout=120)
    from .cadence_compatibility import executable_version, generated_version_error, session_failure
    generation_log = (target / 'generation_session.log').read_text(encoding='utf-8',errors='replace')
    failure = session_failure(generation_log)
    if not dra.is_file() or not dra.with_suffix(".psm").is_file() or run.returncode != 0 or failure:
        raise RuntimeError(f"Generation failed: {failure or 'DRA/PSM missing or nonzero process exit'}; "
                           f"exit={run.returncode}; log={target / 'generation_session.log'}")
    mismatch=generated_version_error(dra,selected_version,generation_log)
    if mismatch: raise RuntimeError(mismatch)
    tsv = target / "actual.tsv"
    exporter = write_actual_export_skill(dra, tsv, target / "export.il")
    replay = write_actual_replay(exporter, target / "export.scr")
    with (target / "readback_session.log").open("wb") as log_stream:
        run = subprocess.run([str(executable), "-nograph", "-symbol", "package", str(dra.resolve()), "-s", str(replay.resolve())],
                             cwd=target, stdout=log_stream, stderr=subprocess.STDOUT, timeout=120)
    log = (target / "readback_session.log").read_text(encoding="utf-8", errors="replace")
    if run.returncode != 0 or session_failure(log): raise RuntimeError('Native readback process failed: '+log[-2000:])
    mismatch = generated_version_error(dra, selected_version, log)
    if mismatch:
        raise RuntimeError(mismatch)
    actual = parse_actual_export(tsv, dra, extraction_log=log)
    diff = compare_spec_to_actual(spec, geometry, actual, gate_mode=gate_mode)
    write_actual_reports(actual, diff, target)
    return {
        "application_version": __version__, "status": diff.status, "gate_mode": gate_mode,
        "target_version": selected_version, "executable_version": actual_version,
        "executable": str(executable), "dra": str(dra),
        "dra_sha256": hashlib.sha256(dra.read_bytes()).hexdigest(),
        "source_spec_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "native_readback_complete": actual.complete,
        "pins": len(actual.pins), "drc": actual.drc_count,
        "checks": len(diff.findings),
        "target_compatibility_verified": actual.complete and diff.status != "fail",
        "publish_allowed": diff.publish_allowed if gate_mode == "release" else False,
        "nonpass": [f.model_dump() for f in diff.findings if f.status != "pass"],
        "scope": "This footprint on this executable only; not all packages or versions.",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("allegro", type=Path)
    parser.add_argument("--target-version", help="Exact required version, e.g. 17.2; mismatch stops before launch")
    parser.add_argument("--gate-mode", choices=["release","review"], default="release",
                        help="Review is isolated staging, never publication approval")
    args = parser.parse_args(argv)
    # Refuse overwriting a previous qualification report.
    if args.output.exists():
        raise FileExistsError(f"Use a new output directory: {args.output}")
    try:
        result = verify_footprint(args.spec, args.output, args.allegro, args.target_version, gate_mode=args.gate_mode)
    except Exception as exc:
        result = {"application_version": __version__, "status": "fail",
                  "target_version": args.target_version, "target_compatibility_verified": False,
                  "publish_allowed": False, "error": str(exc)}
        args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "qualification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if sys.stdout is not None:
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
