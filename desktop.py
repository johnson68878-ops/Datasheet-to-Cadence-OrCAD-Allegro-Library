from __future__ import annotations

import base64
import ctypes
import faulthandler
import functools
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tempfile
from pathlib import Path

import webview

from datasheet_to_allegro import __version__

_FAULT_LOG_HANDLE = None
_INSTANCE_MUTEX = None


def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent


def build_mode() -> str:
    """根据明确的EXE文件名区分交付版与开发测试版。"""
    if not getattr(sys, "frozen", False):
        return "developer"
    return "developer" if "DEVELOPER" in Path(sys.executable).stem.upper() else "portable"


def output_root() -> Path:
    if getattr(sys, "frozen", False):
        folder = "Community_AI_Layout_Output_DEV" if build_mode() == "developer" else "Community_AI_Layout_Output"
        executable_root = Path(sys.executable).resolve().parent
        local_output = executable_root / folder
        # Allegro 17.2/17.4 still routes SKILL and journal paths through an
        # ASCII-only boundary.  Keep normal deployments beside the EXE, but
        # automatically move Cadence-facing work to Public Documents when the
        # installation path contains Chinese, middle dots or other Unicode.
        if str(local_output).isascii():
            return local_output
        public_root = Path(os.environ.get("PUBLIC", r"C:\Users\Public"))
        return public_root / "Documents" / folder
    return Path(__file__).resolve().parent / "output"


def initialize_user_workspace() -> Path:
    """在程序所在目录建立可写的任务目录，不依赖外部文件夹。"""
    root = output_root()
    for relative in ("logs", "generic_staging", "orcad_staging"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    return root


def workstation_settings_path() -> Path:
    # Per-user workstation configuration must remain writable even when the
    # portable EXE is launched from a read-only share, USB image or protected
    # delivery folder.  Cadence never consumes this JSON path, so Unicode is
    # safe here.
    local_app_data = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    return local_app_data / "Community" / "FootprintIntelligenceStudio" / "workstation_settings.json"


def _allegro_version_from_path(path: Path) -> str:
    from datasheet_to_allegro.cadence_compatibility import executable_version
    return executable_version(path) or "未验证"


def _allegro_selection_error(path: Path) -> str:
    common = r"C:\Cadence\SPB_17.2\tools\bin\allegro.exe"
    if path.name.lower() in {"downloadmanager.exe", "dwmupdatecheck.exe"}:
        return (
            f"你选择的是{path.name}（Cadence下载/安装管理器），不是Allegro PCB Editor。"
            f"请选择文件名为allegro.exe的主程序。17.2常见路径：{common}。"
            "若该文件不存在，请联系IT确认Allegro PCB Editor 17.2已完整安装。"
        )
    return (
        f"所选文件不是Allegro PCB Editor主程序：{path.name or path}。"
        f"请选择文件名为allegro.exe；17.2常见路径：{common}。"
    )


def _load_workstation_settings() -> dict[str, object]:
    path = workstation_settings_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _save_allegro_executable(path: Path) -> None:
    settings = _load_workstation_settings()
    settings.update({
        "allegro_executable": str(path.resolve()),
        "allegro_version": _allegro_version_from_path(path),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    destination = workstation_settings_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)


def _detect_allegro_executable() -> tuple[Path | None, str]:
    """按用户配置、环境变量和常见Cadence目录查找Allegro。"""
    configured = _load_workstation_settings().get("allegro_executable")
    if configured:
        candidate = Path(str(configured))
        if candidate.is_file() and candidate.name.lower() == "allegro.exe":
            return candidate.resolve(), "用户配置"

    candidates: list[tuple[Path, str]] = []
    discovered = shutil.which("allegro.exe")
    if discovered:
        candidates.append((Path(discovered), "系统PATH"))
    for env_name in ("CDSROOT", "CADENCE_HOME"):
        value = os.environ.get(env_name)
        if value:
            root = Path(value)
            candidates.extend(((root / "tools" / "bin" / "allegro.exe", f"环境变量 {env_name}"),
                               (root / "bin" / "allegro.exe", f"环境变量 {env_name}")))
    cadence_roots = [
        Path(r"D:\Cadence"), Path(r"C:\Cadence"),
        Path(r"D:\Program Files\Cadence"), Path(r"C:\Program Files\Cadence"),
    ]
    for cadence_root in cadence_roots:
        for folder in ("SPB_17.4", "SPB_17.2", "SPB174", "SPB172", "SPB17.4", "SPB17.2", "SPB_17_4", "SPB_17_2"):
            candidates.append((cadence_root / folder / "tools" / "bin" / "allegro.exe", "标准安装目录"))
        try:
            for pattern in (
                "SPB*/tools/bin/allegro.exe",
                "OrCAD*/tools/bin/allegro.exe",
                "Allegro*/tools/bin/allegro.exe",
            ):
                candidates.extend((path, "Cadence目录扫描") for path in cadence_root.glob(pattern))
        except OSError:
            pass
    seen: set[str] = set()
    for candidate, source in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            return candidate.resolve(), source
    return None, "未配置"


def _detect_capture_executable() -> tuple[Path | None, str]:
    """Locate OrCAD Capture for native schematic-library generation."""
    settings = _load_workstation_settings()
    configured = settings.get("capture_executable")
    if configured:
        candidate = Path(str(configured))
        if candidate.is_file() and candidate.name.lower() == "capture.exe":
            return candidate.resolve(), "用户配置"
    discovered = shutil.which("Capture.exe")
    if discovered:
        return Path(discovered).resolve(), "系统PATH"
    for drive in (Path(r"D:\Cadence"), Path(r"C:\Cadence")):
        for folder in ("SPB_17.4", "SPB_17.2", "SPB174", "SPB172", "SPB17.4", "SPB17.2"):
            candidate = drive / folder / "tools" / "bin" / "Capture.exe"
            if candidate.is_file():
                return candidate.resolve(), "标准安装目录"
    return None, "未配置"


def _windows_powershell_executable() -> str:
    """定位Windows PowerShell，仅供没有WebView窗口时的文件对话框后备。"""
    system_root = Path(os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows")
    candidates = (
        system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
        system_root / "Sysnative" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    for executable in ("powershell.exe", "pwsh.exe"):
        discovered = shutil.which(executable)
        if discovered:
            return discovered
    raise FileNotFoundError("未找到Windows PowerShell后备组件")


def _serialize_verification_failure(item: object) -> dict[str, object]:
    """将VerificationResult转换成稳定的前端契约。"""
    return {
        "code": getattr(item, "check_id"),
        "message": getattr(item, "message"),
        "pages": list(getattr(item, "evidence_pages")),
    }


class LandProofApi:
    def __init__(self) -> None:
        self._window = None
        self._last_output: Path | None = None
        self._last_allegro_target: Path | None = None
        self._last_allegro_host: Path | None = None
        self._last_orcad_target: Path | None = None
        self._allegro_review_process = None
        self._pending_variant_pdf: Path | None = None
        self._build_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._job_id = 0
        self._job_done = True
        self._job_result: dict[str, object] | None = None
        self._job_progress: dict[str, object] = {
            "percent": 0,
            "stage": "等待任务",
            "detail": "本机解析器已就绪。",
            "status": "",
        }

    def health_check(self) -> dict[str, object]:
        allegro, source = _detect_allegro_executable()
        capture, capture_source = _detect_capture_executable()
        version = _allegro_version_from_path(allegro) if allegro else "未配置"
        logging.info("前端桥接健康检查完成，Allegro=%s，来源=%s", allegro, source)
        mode = build_mode()
        return {
            "ok": True,
            "app_version": __version__,
            "message": f"本机解析器已就绪；{'开发测试版' if mode == 'developer' else '用户交付版'}；Allegro {version}：{'已找到' if allegro else '请手动选择'}。",
            "allegro_found": allegro is not None,
            "allegro_path": str(allegro) if allegro else "",
            "allegro_version": version,
            "allegro_source": source,
            "capture_found": capture is not None,
            "capture_path": str(capture) if capture else "",
            "capture_version": _allegro_version_from_path(capture) if capture else "未配置",
            "capture_source": capture_source,
            "build_mode": mode,
            "output_root": str(output_root()),
        }

    def _build_orcad_datasheet(
        self,
        pdf_path: Path,
        schematic_context_path: Path | None = None,
        package_variant: str = "",
        schematic_component_refdes: str = "",
    ) -> dict[str, object]:
        from datasheet_to_allegro.naming import cadence_safe_component
        from datasheet_to_allegro.orcad_library import (
            discover_orcad_package_variants,
            extract_orcad_symbol_spec,
            inspect_schematic_components,
            read_olb_report,
            run_capture_tcl,
            verify_readback,
            write_olb_generator_tcl,
            write_olb_readback_tcl,
            write_symbol_manifest,
        )
        from datasheet_to_allegro.pdf_evidence import (
            analyze_document_evidence,
            require_complete_document_scan,
            write_document_evidence_manifest,
        )

        self._progress(4, "整份PDF深度扫描", "逐页建立Pin、封装、尺寸、架构图与典型应用证据索引。")
        document_profile = analyze_document_evidence(
            pdf_path,
            lambda current, total: self._progress(
                4 + int(12 * current / max(1, total)),
                "整份PDF深度扫描",
                f"已读取第{current}/{total}页；尚未调用OrCAD Capture。",
            ),
        )
        require_complete_document_scan(document_profile)

        variant_pages = sorted(set(
            document_profile.roles.get("ordering_information", [])
            + document_profile.roles.get("pin_table", [])
            + document_profile.roles.get("pin_map", [])
        ))
        self._progress(17, "辨识封装Pin定义", "正在检查多封装Pin Table；尚未调用OrCAD Capture。")
        variants = discover_orcad_package_variants(
            pdf_path,
            evidence_pages=variant_pages,
            progress=lambda current, total: self._progress(
                17 + int(13 * current / max(1, total)),
                "辨识封装Pin定义",
                f"正在检查第{current}/{total}个证据页；尚未调用OrCAD Capture。",
            ),
        )
        if len(variants) > 1 and not package_variant:
            return {
                "ok": False,
                "needs_orcad_variant_selection": True,
                "source_name": pdf_path.name,
                "variants": variants,
                "document_scan": document_profile.to_dict(),
                "message": "Datasheet包含多个封装Pin定义；必须选择实际订购封装后才能建立OLB。",
            }
        if schematic_context_path is not None and schematic_component_refdes:
            selected = next(
                (item for item in inspect_schematic_components(schematic_context_path) if item["refdes"] == schematic_component_refdes.upper()),
                None,
            )
            if selected is None:
                return {"ok": False, "message": f"原理图中找不到所选元件 {schematic_component_refdes}。"}
            chosen_variant = next((item for item in variants if item.get("id") == package_variant), None)
            if chosen_variant and chosen_variant.get("pin_count") and int(chosen_variant["pin_count"]) != int(selected["pin_count"]):
                return {
                    "ok": False,
                    "message": f"封装Pin定义与原理图元件不一致：{chosen_variant['label']}为{chosen_variant['pin_count']} Pin，但{selected['refdes']}实际为{selected['pin_count']} Pin。",
                }

        capture, _source = _detect_capture_executable()
        if capture is None:
            return {"ok": False, "message": "未找到OrCAD Capture 17.2/17.4，无法建立原生OLB。"}
        cadence_root = capture.parents[2]
        logging.info("OrCAD任务开始解析PDF：%s", pdf_path)
        self._progress(31, "读取原理图Pin证据", "正在定位Datasheet中的Pin Table。")

        def extraction_progress(page: int, total: int) -> None:
            percent = 31 + int((page / max(total, 1)) * 17)
            self._progress(percent, "扫描Datasheet Pin Table", f"第{page}/{total}页；界面可继续响应。")

        spec = extract_orcad_symbol_spec(
            pdf_path,
            schematic_context_pdf=schematic_context_path,
            package_variant=package_variant or None,
            schematic_component_refdes=schematic_component_refdes,
            progress=extraction_progress,
        )
        self._progress(48, "PinIR证据Gate完成", f"共确认{len(spec.pins)}个唯一实体Pin；准备建立OLB。")
        task = cadence_safe_component(pdf_path.stem, fallback="orcad_part")
        digest_source = pdf_path.read_bytes()
        digest_source += package_variant.encode("utf-8")
        if schematic_context_path is not None:
            digest_source += schematic_context_path.read_bytes()
        digest = hashlib.sha256(digest_source).hexdigest()[:8]
        bundle = output_root() / "orcad_staging" / f"{task}_{digest}"
        bundle.mkdir(parents=True, exist_ok=True)
        artifact = cadence_safe_component(spec.part_name, fallback="orcad_part")
        olb = bundle / f"{artifact}.olb"
        manifest = bundle / f"{artifact}_pin_evidence.json"
        build_tcl = bundle / f"build_{artifact}.tcl"
        build_log = bundle / f"build_{artifact}.log"
        read_tcl = bundle / f"readback_{artifact}.tcl"
        read_tsv = bundle / f"{artifact}_actual.tsv"
        for stale in (olb, build_log, read_tsv):
            if stale.exists():
                stale.unlink()
        write_symbol_manifest(spec, manifest)
        write_document_evidence_manifest(document_profile, bundle / "document_evidence.json")
        write_olb_generator_tcl(spec, olb, build_tcl, cadence_root=cadence_root, log_path=build_log)
        self._progress(64, "调用OrCAD Capture API", "Cadence Dbo/Tcl正在建立Package、Part、Pin与属性。")
        logging.info("调用Cadence Dbo/Tcl生成OLB：%s", olb)
        generated = run_capture_tcl(build_tcl, cadence_root=cadence_root)
        if generated.returncode or not olb.is_file():
            detail = build_log.read_text(encoding="utf-8", errors="replace") if build_log.exists() else generated.stderr
            return {"ok": False, "message": f"OrCAD OLB生成失败：{detail[-1200:]}"}
        self._progress(84, "Capture实际回读", "用全新Dbo会话重新打开OLB并逐Pin比对。")
        write_olb_readback_tcl(olb, read_tcl, read_tsv, cadence_root=cadence_root)
        actual = run_capture_tcl(read_tcl, cadence_root=cadence_root)
        if actual.returncode or not read_tsv.is_file():
            return {"ok": False, "message": f"OLB已生成但Capture回读失败：{actual.stderr[-1200:]}"}
        rows = read_olb_report(read_tsv)
        errors = verify_readback(spec, rows)
        if errors:
            return {"ok": False, "message": "OLB实际回读Gate失败：" + "；".join(errors[:8]), "output_dir": str(bundle)}
        self._last_output = bundle
        self._last_orcad_target = olb
        import string

        section_counts = {
            letter: sum(pin.section == letter for pin in spec.pins)
            for letter in string.ascii_uppercase
            if any(pin.section == letter for pin in spec.pins)
        }
        section_total = sum(bool(count) for count in section_counts.values())
        self._progress(100, "原生OLB验证通过", f"{len(spec.pins)} Pin、{section_total}个Part已由Capture API实际读回。")
        logging.info("OrCAD OLB生成及回读通过：%s，pins=%s", olb, len(spec.pins))
        return {
            "ok": True,
            "source_name": pdf_path.name,
            "part_name": spec.part_name,
            "pin_count": len(spec.pins),
            "section_counts": section_counts,
            "olb_path": str(olb),
            "output_dir": str(bundle),
            "capture_api_verified": True,
            "package_variant": spec.package_variant,
            "context_matched_pins": spec.context_matched_pins,
            "architecture_evidence_pages": spec.architecture_evidence_pages,
            "schematic_component_refdes": spec.schematic_component_refdes,
            "document_scan": document_profile.to_dict(),
            "review_warnings": spec.review_warnings,
            "message": "已生成原生OLB并通过逐Pin回读。正式发布仍需EE审核。" + " ".join(spec.review_warnings),
        }

    def _build_orcad_schematic_components(
        self,
        schematic_path: Path,
        component_refdes: str = "*",
    ) -> dict[str, object]:
        """Build one or every selected schematic component without a datasheet."""

        from datasheet_to_allegro.naming import cadence_safe_component
        from datasheet_to_allegro.orcad_library import (
            extract_orcad_symbol_spec_from_schematic,
            inspect_schematic_components,
            read_olb_report,
            run_capture_tcl,
            verify_readback,
            write_olb_generator_tcl,
            write_olb_readback_tcl,
            write_symbol_manifest,
        )

        self._progress(4, "读取原理图零件证据", "正在确认RefDes、Pin Number、Pin Name与多Part标记。")
        inventory = inspect_schematic_components(
            schematic_path,
            lambda current, total: self._progress(
                4 + int(16 * current / max(1, total)),
                "读取原理图零件证据",
                f"第{current}/{total}页；不需要Datasheet。",
            ),
        )
        if component_refdes == "*":
            targets = inventory
        else:
            targets = [item for item in inventory if item["refdes"] == component_refdes.upper()]
        if not targets:
            return {"ok": False, "message": f"原理图中找不到所选元件 {component_refdes}。"}

        capture, _source = _detect_capture_executable()
        if capture is None:
            return {"ok": False, "message": "未找到OrCAD Capture 17.2/17.4，无法建立原生OLB。"}
        cadence_root = capture.parents[2]
        specs = []
        seen_packages: dict[str, object] = {}
        for index, target in enumerate(targets, start=1):
            spec = extract_orcad_symbol_spec_from_schematic(
                schematic_path,
                str(target["refdes"]),
                component=target,
                progress=lambda current, total, index=index: self._progress(
                    20 + int(25 * ((index - 1) + current / max(1, total)) / max(1, len(targets))),
                    "重建原理图Symbol",
                    f"{target['refdes']}：提取可见Pin名称与Part分组。",
                ),
            )
            # Capture 17.2/17.4 package names are kept deliberately short;
            # long schematic descriptions are preserved in the evidence JSON.
            spec.part_name = cadence_safe_component(spec.part_name, fallback=str(target["refdes"]), max_length=30)
            signature = tuple((pin.number, pin.name, pin.section) for pin in spec.pins)
            previous = seen_packages.get(spec.part_name)
            if previous == signature:
                continue
            if previous is not None:
                spec.part_name = cadence_safe_component(
                    f"{spec.part_name}_{target['refdes']}",
                    fallback=str(target["refdes"]),
                    max_length=30,
                )
            seen_packages[spec.part_name] = signature
            specs.append(spec)
        if not specs:
            return {"ok": False, "message": "所选原理图没有可建立的唯一零件。"}

        task = cadence_safe_component(schematic_path.stem, fallback="schematic")
        digest = hashlib.sha256(schematic_path.read_bytes() + component_refdes.encode("utf-8")).hexdigest()[:8]
        bundle = output_root() / "orcad_staging" / f"{task}_schematic_{digest}"
        bundle.mkdir(parents=True, exist_ok=True)
        olb = bundle / f"{task}_SCHEMATIC.OLB"
        read_tcl = bundle / "readback_all.tcl"
        read_tsv = bundle / "actual_all.tsv"
        for stale in (olb, read_tsv):
            if stale.exists():
                stale.unlink()

        for index, spec in enumerate(specs, start=1):
            artifact = cadence_safe_component(spec.part_name, fallback=f"PART_{index}")
            write_symbol_manifest(spec, bundle / f"{artifact}_pin_evidence.json")
            build_tcl = bundle / f"build_{index:03d}_{artifact}.tcl"
            build_log = bundle / f"build_{index:03d}_{artifact}.log"
            write_olb_generator_tcl(
                spec,
                olb,
                build_tcl,
                cadence_root=cadence_root,
                log_path=build_log,
                append=index > 1,
            )
            self._progress(
                45 + int(35 * index / len(specs)),
                "写入同一个OrCAD OLB",
                f"{index}/{len(specs)}：{spec.part_name}，{len(spec.pins)} Pin。",
            )
            generated = run_capture_tcl(build_tcl, cadence_root=cadence_root)
            if generated.returncode or not olb.is_file():
                detail = build_log.read_text(encoding="utf-8", errors="replace") if build_log.exists() else generated.stderr
                return {"ok": False, "message": f"{spec.part_name} 写入OLB失败：{detail[-1200:]}", "output_dir": str(bundle)}

        self._progress(84, "Capture实际回读", f"正在回读同一OLB中的{len(specs)}个Package。")
        write_olb_readback_tcl(olb, read_tcl, read_tsv, cadence_root=cadence_root)
        actual = run_capture_tcl(read_tcl, cadence_root=cadence_root)
        if actual.returncode or not read_tsv.is_file():
            return {"ok": False, "message": f"OLB已生成但Capture回读失败：{actual.stderr[-1200:]}", "output_dir": str(bundle)}
        rows = read_olb_report(read_tsv)
        errors: list[str] = []
        for spec in specs:
            package_rows = [row for row in rows if row.get("package") == spec.part_name]
            errors.extend(f"{spec.part_name}: {message}" for message in verify_readback(spec, package_rows))
        if errors:
            return {"ok": False, "message": "OLB实际回读Gate失败：" + "；".join(errors[:12]), "output_dir": str(bundle)}

        self._last_output = bundle
        self._last_orcad_target = olb
        total_pins = sum(len(spec.pins) for spec in specs)
        self._progress(100, "原理图OLB验证通过", f"{len(specs)}个零件、{total_pins} Pin已由Capture API实际读回。")
        return {
            "ok": True,
            "source_name": schematic_path.name,
            "part_name": specs[0].part_name if len(specs) == 1 else "全部原理图零件",
            "pin_count": total_pins,
            "package_count": len(specs),
            "section_counts": {},
            "olb_path": str(olb),
            "output_dir": str(bundle),
            "capture_api_verified": True,
            "package_variant": "SCHEMATIC PDF",
            "context_matched_pins": total_pins,
            "architecture_evidence_pages": [],
            "message": f"已从原理图PDF直接生成{len(specs)}个零件到同一个OLB，并通过Capture逐Pin回读；未读取Datasheet。",
        }

    def _progress(self, percent: int, stage: str, detail: str, status: str = "") -> None:
        # 后台线程不直接操作WebView，前端通过job_status轮询这一小段状态。
        with self._state_lock:
            self._job_progress = {
                "percent": percent,
                "stage": stage,
                "detail": detail,
                "status": status,
            }

    @staticmethod
    def _allegro_executable() -> Path:
        path, _source = _detect_allegro_executable()
        return path if path is not None else output_root() / "ALLEGRO_NOT_CONFIGURED.exe"

    @staticmethod
    def _write_review_replay(generated_dra: Path) -> Path:
        show_replay = generated_dra.with_name("show_generated_footprint.scr")
        review_colors = generated_dra.with_name("review_display_colors.il")
        review_command = f'skill load("{review_colors.resolve().as_posix()}")\n' if review_colors.exists() else ""
        show_replay.write_text(review_command + "zoom fit\nzoom out\nzoom out\n", encoding="ascii")
        return show_replay

    def _build_datasheet(self, pdf_path: Path, selected_variant_index: int | None = None) -> dict[str, object]:
        self._progress(5, "已选择PDF", "正在加载解析引擎。")
        from datasheet_to_allegro.allegro import generate_geometry_skill
        from datasheet_to_allegro.audit import audit_as_dict, audit_geometry
        from datasheet_to_allegro.geometry import build_geometry
        from datasheet_to_allegro.generation_gate import generation_blockers, review_generation_blockers
        from datasheet_to_allegro.industry_defaults import (
            apply_staging_manufacturing_profile,
        )
        from datasheet_to_allegro.naming import cadence_safe_component
        from datasheet_to_allegro.pipeline import inspect_datasheet
        from datasheet_to_allegro.pdf_evidence import (
            analyze_document_evidence,
            require_complete_document_scan,
            write_document_evidence_manifest,
        )
        from datasheet_to_allegro.render import write_bundle
        from datasheet_to_allegro.universal_extractor import write_visual_evidence

        self._last_allegro_target = None
        self._last_allegro_host = None
        self._progress(6, "PDF已载入", pdf_path.name)
        try:
            self._progress(7, "整份PDF深度扫描", "逐页建立封装、Pin Map、尺寸表、Land Pattern与制造规则索引。")
            document_profile = analyze_document_evidence(
                pdf_path,
                lambda current, total: self._progress(
                    7 + int(7 * current / max(1, total)),
                    "整份PDF深度扫描",
                    f"已读取第{current}/{total}页；尚未调用Allegro PCB Editor。",
                ),
            )
            require_complete_document_scan(document_profile)
            self._progress(14, "读取PDF文字与页面", "正在建立文档指纹并定位封装相关页面。")
            fingerprint, specs = inspect_datasheet(
                pdf_path,
                lambda current, total: self._progress(
                    14 + int(20 * current / max(1, total)),
                    "扫描Datasheet",
                    f"正在分析第{current}/{total}页，并定位封装图、Pin Map与尺寸表。",
                ),
            )
            self._progress(36, "识别封装与证据", f"识别结果：{'、'.join(fingerprint.detected_families) or '未识别'}；共{fingerprint.page_count}页。")
        except Exception as exc:  # noqa: BLE001
            self._progress(100, "PDF读取失败", str(exc), "error")
            return {"ok": False, "message": f"PDF 读取失败：{exc}"}
        safe_task_name = cadence_safe_component(pdf_path.stem, fallback="datasheet")
        if not specs:
            self._progress(100, "封裝身份未確認", "保留掃描證據；停止生成，不使用佔位封裝。", "error")
            self._last_allegro_target = None
            return {"ok": False, "review_hold": True, "message": "PDF已讀取，但封裝身份尚未辨識；不建立猜測封裝，不呼叫Allegro。"}
        if len(specs) > 1 and selected_variant_index is None:
            self._pending_variant_pdf = pdf_path
            variants = []
            for index, item in enumerate(specs):
                evidence_pages = sorted({
                    evidence.page
                    for evidence_items in item.source_evidence.values()
                    for evidence in evidence_items
                })
                variants.append({
                    "index": index,
                    "package_type": item.package_type,
                    "package_code": item.package_code or "未标注",
                    "pin_count": item.pin_count,
                    "evidence_pages": evidence_pages,
                    "risk_count": len(item.unresolved_items),
                    "label": f"{item.package_code or item.package_type} · {item.pin_count} Pin/Ball",
                })
            self._progress(100, "请选择封装Variant", f"Datasheet包含{len(variants)}个封装候选；选择后才会调用Allegro。")
            return {
                "ok": True,
                "needs_variant_selection": True,
                "source_name": pdf_path.name,
                "message": "同一Datasheet包含多个封装选项，请选择实际订购封装。",
                "variants": variants,
                "document_scan": document_profile.to_dict(),
            }
        if selected_variant_index is not None:
            if not 0 <= selected_variant_index < len(specs):
                return {"ok": False, "message": "选择的封装Variant已失效，请重新导入Datasheet。"}
            selected = specs[selected_variant_index]
            # The full VariantGraph remains embedded in the selected spec's
            # Document IR. Only the confirmed Variant may enter one Allegro
            # bundle; otherwise candidates sharing a part number overwrite the
            # same output directory and mix evidence across packages.
            specs = [selected]
            self._pending_variant_pdf = None
        if specs:
            self._progress(52, "建立可追溯规格", f"提取到{len(specs)}个封装选项，正在写入尺寸证据。")
            specs = [apply_staging_manufacturing_profile(item) for item in specs]
            spec = specs[0]
            from datasheet_to_allegro.draft_readiness import assess_draft_readiness
            from datasheet_to_allegro.models import UnresolvedItem
            readiness = assess_draft_readiness(spec)
            preflight_blockers = [UnresolvedItem(**item) for item in readiness['blockers']]
            if preflight_blockers:
                self._last_allegro_target = None
                review_dir = output_root() / "source_holds" / safe_task_name
                review_dir.mkdir(parents=True, exist_ok=True)
                write_bundle(spec, review_dir)
                (review_dir / 'draft_readiness.json').write_text(
                    json.dumps(readiness, ensure_ascii=False, indent=2), encoding='utf-8')
                self._last_output = review_dir
                self._progress(100, "生成已停止（证据未通过）", "来源证据检查未通过；未调用Allegro。详见阻挡原因。", "error")
                return {"ok": False, "review_hold": True, "message": "來源尚有未證實項；已停止DRA生成。 " + "；".join(item.message for item in preflight_blockers),
                        "output_dir": str(review_dir), "blocking_items": [item.model_dump() for item in preflight_blockers]}
            verification_counts = {
                state: sum(1 for item in spec.verification_results if item.status == state)
                for state in ("pass", "warning", "fail")
            }
            session_dir = output_root() / "generic_staging" / safe_task_name
            session_dir.mkdir(parents=True, exist_ok=True)
            (session_dir / 'draft_readiness.json').write_text(
                json.dumps(readiness, ensure_ascii=False, indent=2), encoding='utf-8')
            (session_dir / "source_manifest.json").write_text(
                json.dumps({
                    "source_file": str(pdf_path),
                    "source_name_original": pdf_path.name,
                    "cadence_safe_task_directory": safe_task_name,
                    "pdf_sha256": spec.source_sha256 or hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            write_document_evidence_manifest(document_profile, session_dir / "document_evidence.json")
            bundle_dirs = [write_bundle(item, session_dir) for item in specs]
            bundle_dir = bundle_dirs[0]
            self._progress(58, "逐页视觉核对原始图纸", "正在渲染所有被引用的PDF页，并建立字段、页码、图表与原文证据清单。")
            visual_evidence_manifest = write_visual_evidence(
                pdf_path, spec, bundle_dir / "pdf_visual_evidence"
            )
            self._last_output = session_dir
            family_type = {
                "QFN": "qfn", "DFN": "qfn", "SON": "qfn", "QFP": "qfp",
                "SOIC": "qfp", "SOT": "qfp", "BGA": "array", "WLCSP": "array",
                "LGA": "array", "THT": "tht", "CONNECTOR": "tht",
            }.get(spec.package_type.upper(), "custom")
            release_blockers = generation_blockers(spec)
            skill_path: Path | None = None
            geometry_error: str | None = None
            geometry_payload: dict[str, object] | None = None
            audit_payload: list[dict[str, object]] = []
            try:
                self._progress(68, "验证几何与Pin编号", "正在检查已提取值与风险暂存推导；未证实项目不会冒充Datasheet事实。")
                geometry = build_geometry(spec)
                geometry_payload = geometry.as_dict()
                findings = audit_geometry(spec, geometry, gate_mode='review')
                audit_payload = audit_as_dict(findings)
                (bundle_dir / "geometry_audit.json").write_text(
                    json.dumps(audit_payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                failed_audits = [item for item in findings if item.status == "fail"]
                if failed_audits:
                    raise ValueError("Geometry audit failed: " + "; ".join(str(item) for item in failed_audits))
                self._progress(80, "生成Allegro SKILL", "正在生成Padstack与Package Symbol暂存脚本。")
                skill_path = generate_geometry_skill(
                    spec,
                    geometry,
                    bundle_dir / "generated_footprint_staging.il",
                    approved_by="REVIEW_STAGING_NOT_RELEASE_APPROVED",
                    gate_mode="review",
                )
            except Exception as exc:
                self._last_allegro_target = None
                geometry_error = str(exc)
                (bundle_dir / "generation_blocked.json").write_text(
                    json.dumps({"code": "GEOMETRY_NOT_PROVABLE", "message": geometry_error,
                                "action": "核對來源與幾何，禁止切換成猜測封裝。"}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                self._progress(100, "已停止生成", geometry_error, "error")
                return {"ok": False, "review_hold": True, "message": geometry_error,
                        "output_dir": str(session_dir), "visual_evidence_manifest": str(visual_evidence_manifest)}
            verification_counts = {
                state: sum(1 for item in spec.verification_results if item.status == state)
                for state in ("pass", "warning", "fail")
            }
            risk_items = list(spec.unresolved_items)
            preview = {
                "title": f"{spec.part_number} · {spec.package_type}",
                "partNumber": spec.part_number,
                "badge": "工程审核暂存" if risk_items else "闭环验证暂存",
                "type": family_type,
                "rows": len(spec.row_names) or 8,
                "cols": spec.column_count or 8,
                "pins": spec.pin_count,
                "geometry": geometry_payload,
                "metrics": [
                    ["封装族", spec.package_type],
                    ["Pin / Ball", str(spec.pin_count or "未解析")],
                    ["证据检查", f"{verification_counts['pass']} PASS / {verification_counts['fail']} FAIL"],
                    ["工程风险", str(len(risk_items))],
                ],
            }
            if skill_path is None:
                raise RuntimeError("证据驱动后端未产生Allegro SKILL脚本")
            self._last_allegro_target = skill_path
            selected_allegro = self._allegro_executable()
            selected_version = _allegro_version_from_path(selected_allegro) if selected_allegro.exists() else "未配置"
            self._progress(91, f"调用Allegro {selected_version}生成", "正在执行已通过证据门与几何审计的暂存SKILL。")
            allegro_result = self.open_in_allegro()
            actual_result: dict[str, object] = {
                "ok": False,
                "status": "fail",
                "message": "Allegro未成功生成DRA，无法执行实际数据库回读。",
            }
            if allegro_result["ok"] and allegro_result.get("target"):
                self._progress(96, "回读Allegro实际数据库", "正在用全新Allegro会话提取Pin、Padstack、坐标、图层和DRC。")
                actual_result = self._verify_actual_database(
                    Path(str(allegro_result["target"])), spec, geometry, bundle_dir
                )
            final_ok = bool(allegro_result["ok"] and actual_result.get("ok"))
            final_message = str(actual_result.get("message") if allegro_result["ok"] else allegro_result["message"])
            blocking_payload = []
            seen_blocking_codes: set[str] = set()
            for item in list(release_blockers) + risk_items:
                if item.code in seen_blocking_codes:
                    continue
                seen_blocking_codes.add(item.code)
                blocking_payload.append({
                    "code": item.code,
                    "message": item.message,
                    "action": item.required_action,
                })
            self._progress(100, "处理完成" if final_ok else "验证未通过", final_message, "" if final_ok else "error")
            return {
                "ok": final_ok,
                "generated_kind": "allegro_verified_staging" if final_ok else "allegro_unverified_staging",
                "source_name": pdf_path.name,
                "part_number": spec.part_number,
                "package_type": spec.package_type,
                "message": final_message,
                "output_dir": str(session_dir),
                "skill": str(skill_path),
                "visual_evidence_manifest": str(visual_evidence_manifest),
                "document_scan": document_profile.to_dict(),
                "preview_model": preview,
                "allegro_ready": final_ok,
                "allegro_launched": bool(allegro_result["ok"]),
                "actual_verification": actual_result,
                "review_hold": bool(release_blockers or failed_audits or risk_items or verification_counts["fail"] or verification_counts["warning"] or not final_ok),
                "generated_with_risk": bool(release_blockers or failed_audits or risk_items or verification_counts["fail"] or verification_counts["warning"] or not final_ok),
                "blocking_items": blocking_payload + [
                    {
                        "code": item.code,
                        "message": f"暂存几何审计未通过：{item.message}",
                        "action": "在Allegro与原始Datasheet中逐项核对，禁止发布正式Library。",
                    }
                    for item in failed_audits
                ] + ([{
                    "code": "ALLEGRO_EXECUTION_WARNING",
                    "message": allegro_result["message"],
                    "action": "检查Allegro许可证、输出日志和暂存目录；不要发布正式Library。",
                }] if not allegro_result["ok"] else []) + ([{
                    "code": "ALLEGRO_ACTUAL_DIFF_FAILED",
                    "message": str(actual_result.get("message") or "Allegro实际数据库与计划几何不一致。"),
                    "action": "打开spec_vs_allegro_actual.md，修正差异后重新生成。",
                }] if allegro_result["ok"] and not actual_result.get("ok") else []),
                "verification_failures": [
                    _serialize_verification_failure(item)
                    for item in spec.verification_results if item.status == "fail"
                ],
                "verification_summary": verification_counts,
                "geometry_audit": audit_payload,
            }

    def _verify_actual_database(self, dra_path: Path, spec, geometry, bundle_dir: Path) -> dict[str, object]:
        """Reopen the generated DRA in a fresh Allegro process and compare actual DB state."""
        from datasheet_to_allegro.actual_verification import (
            compare_spec_to_actual,
            parse_actual_export,
            write_actual_export_skill,
            write_actual_replay,
            write_actual_reports,
        )

        allegro = self._allegro_executable()
        export_tsv = bundle_dir / "allegro_actual_export.tsv"
        export_il = write_actual_export_skill(dra_path, export_tsv, bundle_dir / "export_allegro_actual.il")
        replay = write_actual_replay(export_il, bundle_dir / "export_allegro_actual.scr")
        previous_review = self._allegro_review_process
        if previous_review is not None and previous_review.poll() is None:
            previous_review.terminate()
            try:
                previous_review.wait(timeout=8)
            except subprocess.TimeoutExpired:
                return {"ok": False, "status": "fail", "message": "无法关闭当前评审窗口，实际数据库回读未执行。"}
        self._allegro_review_process = None
        if export_tsv.exists():
            export_tsv.unlink()
        try:
            completed = subprocess.run(
                [str(allegro), "-nograph", "-symbol", "package", str(dra_path), "-s", str(replay)],
                cwd=bundle_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "status": "fail", "message": f"Allegro实际数据库回读启动失败：{exc}"}
        log_text = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        (bundle_dir / "allegro_actual_session.log").write_text(log_text, encoding="utf-8")
        from datasheet_to_allegro.cadence_compatibility import session_failure, generated_version_error
        version_error = generated_version_error(dra_path, _allegro_version_from_path(allegro), log_text)
        if completed.returncode != 0 or not export_tsv.is_file() or session_failure(log_text) or version_error:
            return {
                "ok": False,
                "status": "fail",
                "message": "Allegro新会话未产生实际数据库导出；Footprint保持HOLD，请查看allegro_actual_session.log。",
                "return_code": completed.returncode,
            }
        try:
            actual = parse_actual_export(export_tsv, dra_path, extraction_log=log_text[-4000:])
            diff = compare_spec_to_actual(spec, geometry, actual, gate_mode="review")
            write_actual_reports(actual, diff, bundle_dir)
        except Exception as exc:  # noqa: BLE001
            logging.exception("解析Allegro实际数据库导出失败")
            return {"ok": False, "status": "fail", "message": f"Allegro实际数据库导出无法解析：{exc}"}
        self._last_allegro_target = dra_path
        review_result = self.open_in_allegro()
        return {
            "ok": diff.status == "pass",
            "status": diff.status,
            "publish_allowed": diff.publish_allowed,
            "message": (
                f"Allegro实际数据库闭环验证通过，并已保持打开：{dra_path.name}。正式Library仍需工程审批。"
                if diff.status == "pass"
                else f"Allegro已生成，但实际数据库比对为{diff.status.upper()}；已保持打开并生成差异报告。"
            ),
            "actual_ir": str(bundle_dir / "allegro_actual_ir.json"),
            "diff_report": str(bundle_dir / "spec_vs_allegro_actual.md"),
            "review_window_kept_open": bool(review_result.get("ok")),
        }

    def choose_datasheet(self) -> dict[str, object]:
        """使用当前程序窗口的Windows原生文件对话框选择Datasheet。"""
        if self._build_lock.locked():
            return {"ok": False, "busy": True, "message": "已有Datasheet任务正在执行。"}
        if self._window is not None:
            try:
                selected_files = self._window.create_file_dialog(
                    webview.FileDialog.OPEN,
                    allow_multiple=False,
                    file_types=("PDF Datasheet (*.pdf)",),
                )
                if not selected_files:
                    return {"ok": False, "cancelled": True, "message": "已取消选择。"}
                pdf_path = Path(str(selected_files[0])).resolve()
                if not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf":
                    return {"ok": False, "message": "所选文件不是有效的PDF。"}
                return {"ok": True, "path": str(pdf_path), "name": pdf_path.name}
            except Exception:  # noqa: BLE001
                logging.exception("程序内置Datasheet选择窗口失败，尝试系统后备")
        script = r"""
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Filter = 'PDF Datasheet (*.pdf)|*.pdf'
$dialog.Title = '选择元件 Datasheet'
$dialog.Multiselect = $false
$dialog.CheckFileExists = $true
$dialog.RestoreDirectory = $true
$owner = New-Object System.Windows.Forms.Form
$owner.ShowInTaskbar = $false
$owner.TopMost = $true
$owner.Opacity = 0
$owner.Width = 1
$owner.Height = 1
$owner.Show()
$result = $dialog.ShowDialog($owner)
$owner.Close()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
  [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($dialog.FileName))
}
"""
        try:
            completed = subprocess.run(
                [_windows_powershell_executable(), "-NoProfile", "-STA", "-Command", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            encoded = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else ""
            if not encoded:
                return {"ok": False, "cancelled": True, "message": "已取消选择。"}
            selected = base64.b64decode(encoded).decode("utf-8")
            pdf_path = Path(selected).resolve()
            if not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf":
                return {"ok": False, "message": "所选文件不是有效的PDF。"}
            return {"ok": True, "path": str(pdf_path), "name": pdf_path.name}
        except Exception as exc:  # noqa: BLE001
            logging.exception("打开Datasheet选择窗口失败")
            return {"ok": False, "message": f"无法打开Windows文件选择窗口：{exc}。请重新启动程序；无需配置Allegro路径即可选择PDF。"}

    def choose_allegro_executable(self) -> dict[str, object]:
        """允许工作站选择17.2/17.4或其他兼容安装，并持久化到输出目录。"""
        current, _source = _detect_allegro_executable()
        initial_directory = str(current.parent) if current else r"C:\Cadence"
        if self._window is not None:
            try:
                selected_files = self._window.create_file_dialog(
                    webview.FileDialog.OPEN,
                    directory=initial_directory,
                    allow_multiple=False,
                    # pywebview requires a wildcard extension in every filter.
                    # The previous literal "allegro.exe" filter raised a
                    # ValueError before Windows could display the dialog.
                    file_types=("Allegro PCB Editor (*.exe)",),
                )
                if not selected_files:
                    return {"ok": False, "cancelled": True, "message": "已取消选择Allegro。"}
                selected = Path(str(selected_files[0])).resolve()
                if not selected.is_file() or selected.name.lower() != "allegro.exe":
                    return {"ok": False, "message": _allegro_selection_error(selected)}
                _save_allegro_executable(selected)
                version = _allegro_version_from_path(selected)
                logging.info("用户配置Allegro：%s（版本=%s）", selected, version)
                return {
                    "ok": True,
                    "allegro_found": True,
                    "allegro_path": str(selected),
                    "allegro_version": version,
                    "allegro_source": "用户配置",
                    "message": f"已保存Allegro {version}路径；后续生成、回读与验证均使用此版本。",
                }
            except Exception:  # noqa: BLE001
                logging.exception("程序内置Allegro选择窗口失败，尝试系统后备")
        encoded_initial = base64.b64encode(initial_directory.encode("utf-8")).decode("ascii")
        script = rf"""
Add-Type -AssemblyName System.Windows.Forms
$initial = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded_initial}'))
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Filter = 'Allegro PCB Editor (allegro.exe)|allegro.exe'
$dialog.Title = '选择 Allegro PCB Editor 执行文件'
$dialog.Multiselect = $false
$dialog.CheckFileExists = $true
$dialog.RestoreDirectory = $true
if (Test-Path -LiteralPath $initial) {{ $dialog.InitialDirectory = $initial }}
$owner = New-Object System.Windows.Forms.Form
$owner.ShowInTaskbar = $false
$owner.TopMost = $true
$owner.Opacity = 0
$owner.Width = 1
$owner.Height = 1
$owner.Show()
$result = $dialog.ShowDialog($owner)
$owner.Close()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {{
  [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($dialog.FileName))
}}
"""
        try:
            completed = subprocess.run(
                [_windows_powershell_executable(), "-NoProfile", "-STA", "-Command", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            encoded = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else ""
            if not encoded:
                return {"ok": False, "cancelled": True, "message": "已取消选择Allegro。"}
            selected = Path(base64.b64decode(encoded).decode("utf-8")).resolve()
            if not selected.is_file() or selected.name.lower() != "allegro.exe":
                return {"ok": False, "message": _allegro_selection_error(selected)}
            _save_allegro_executable(selected)
            version = _allegro_version_from_path(selected)
            logging.info("用户配置Allegro：%s（版本=%s）", selected, version)
            return {
                "ok": True,
                "allegro_found": True,
                "allegro_path": str(selected),
                "allegro_version": version,
                "allegro_source": "用户配置",
                "message": f"已保存Allegro {version}路径；后续生成、回读与验证均使用此版本。",
            }
        except Exception as exc:  # noqa: BLE001
            logging.exception("选择Allegro执行文件失败")
            return {"ok": False, "message": f"无法保存Allegro路径：{exc}"}

    def start_build(self, source_file: str) -> dict[str, object]:
        """启动单一后台任务，并立即把控制权还给界面。"""
        pdf_path = Path(source_file).resolve()
        if not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf":
            return {"ok": False, "message": "Datasheet路径无效或文件不存在。"}
        if not self._build_lock.acquire(blocking=False):
            return {"ok": False, "busy": True, "message": "已有Datasheet任务正在执行。"}
        with self._state_lock:
            self._job_id += 1
            job_id = self._job_id
            self._job_done = False
            self._job_result = None
            self._job_progress = {
                "percent": 3,
                "stage": "任务已进入后台",
                "detail": pdf_path.name,
                "status": "",
            }

        def worker() -> None:
            try:
                result = self._build_datasheet(pdf_path)
            except Exception as exc:  # noqa: BLE001
                logging.exception("Datasheet后台处理发生未捕获异常")
                self._progress(100, "处理失败", str(exc), "error")
                result = {"ok": False, "message": f"处理失败：{exc}"}
            finally:
                with self._state_lock:
                    self._job_result = result
                    self._job_done = True
                self._build_lock.release()

        threading.Thread(target=worker, name=f"datasheet-job-{job_id}", daemon=True).start()
        return {"ok": True, "job_id": job_id, "source_name": pdf_path.name}

    def start_orcad_build(
        self,
        source_file: str,
        schematic_context_file: str = "",
        package_variant: str = "",
        schematic_component_refdes: str = "",
    ) -> dict[str, object]:
        """Build an isolated native Capture OLB in a background job."""
        pdf_path = Path(source_file).resolve()
        if not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf":
            return {"ok": False, "message": "Datasheet路径无效或文件不存在。"}
        context_path = Path(schematic_context_file).resolve() if schematic_context_file else None
        if context_path is not None and (not context_path.is_file() or context_path.suffix.lower() != ".pdf"):
            return {"ok": False, "message": "原理图设计上下文PDF路径无效或文件不存在。"}
        if not self._build_lock.acquire(blocking=False):
            return {"ok": False, "busy": True, "message": "已有Cadence建库任务正在执行。"}
        with self._state_lock:
            self._job_id += 1
            job_id = self._job_id
            self._job_done = False
            self._job_result = None
            self._job_progress = {"percent": 3, "stage": "OrCAD任务已进入后台", "detail": pdf_path.name, "status": ""}

        def worker() -> None:
            try:
                result = self._build_orcad_datasheet(pdf_path, context_path, package_variant, schematic_component_refdes)
            except Exception as exc:  # noqa: BLE001
                logging.exception("OrCAD后台建库发生未捕获异常")
                self._progress(100, "OrCAD建库失败", str(exc), "error")
                result = {"ok": False, "message": f"OrCAD建库失败：{exc}"}
            finally:
                with self._state_lock:
                    self._job_result = result
                    self._job_done = True
                self._build_lock.release()

        threading.Thread(target=worker, name=f"orcad-job-{job_id}", daemon=True).start()
        return {"ok": True, "job_id": job_id, "source_name": pdf_path.name}

    def start_orcad_schematic_build(
        self,
        schematic_file: str,
        component_refdes: str = "*",
    ) -> dict[str, object]:
        """Build one or all symbols directly from an EDA schematic PDF."""

        path = Path(schematic_file).resolve()
        if not path.is_file() or path.suffix.lower() != ".pdf":
            return {"ok": False, "message": "原理图PDF路径无效或文件不存在。"}
        target = (component_refdes or "*").upper()
        if not self._build_lock.acquire(blocking=False):
            return {"ok": False, "busy": True, "message": "已有Cadence建库任务正在执行。"}
        with self._state_lock:
            self._job_id += 1
            job_id = self._job_id
            self._job_done = False
            self._job_result = None
            self._job_progress = {
                "percent": 3,
                "stage": "原理图直接建库已进入后台",
                "detail": "全部零件" if target == "*" else target,
                "status": "",
            }

        def worker() -> None:
            try:
                result = self._build_orcad_schematic_components(path, target)
            except Exception as exc:  # noqa: BLE001
                logging.exception("原理图直接建库发生未捕获异常")
                self._progress(100, "原理图建库失败", str(exc), "error")
                result = {"ok": False, "message": f"原理图建库失败：{exc}"}
            finally:
                with self._state_lock:
                    self._job_result = result
                    self._job_done = True
                self._build_lock.release()

        threading.Thread(target=worker, name=f"schematic-build-{job_id}", daemon=True).start()
        return {"ok": True, "job_id": job_id, "source_name": path.name}

    def inspect_orcad_schematic(self, schematic_file: str) -> dict[str, object]:
        """Read an EDA-exported schematic PDF and return selectable components."""
        from datasheet_to_allegro.orcad_library import inspect_schematic_components

        path = Path(schematic_file).resolve()
        if not path.is_file() or path.suffix.lower() != ".pdf":
            return {"ok": False, "message": "原理图PDF路径无效或文件不存在。"}
        try:
            components = inspect_schematic_components(path)
        except Exception as exc:  # noqa: BLE001
            logging.exception("原理图元件盘点失败")
            return {"ok": False, "message": f"无法读取原理图元件：{exc}"}
        if not components:
            return {"ok": False, "message": "原理图PDF中未找到可验证的RefDes与Pin标记；请确认这是EDA导出的可搜索PDF。"}
        return {
            "ok": True,
            "source_name": path.name,
            "component_count": len(components),
            "components": components,
            "message": f"已识别{len(components)}个可建立原理图零件的候选，请选择目标元件。",
        }

    def start_orcad_schematic_inspection(self, schematic_file: str) -> dict[str, object]:
        """Inventory a schematic PDF off the WebView bridge thread."""

        from datasheet_to_allegro.orcad_library import inspect_schematic_components

        path = Path(schematic_file).resolve()
        if not path.is_file() or path.suffix.lower() != ".pdf":
            return {"ok": False, "message": "原理图PDF路径无效或文件不存在。"}
        if not self._build_lock.acquire(blocking=False):
            return {"ok": False, "busy": True, "message": "已有Cadence建库任务正在执行。"}
        with self._state_lock:
            self._job_id += 1
            job_id = self._job_id
            self._job_done = False
            self._job_result = None
            self._job_progress = {
                "percent": 2,
                "stage": "原理图盘点已进入后台",
                "detail": path.name,
                "status": "",
            }

        def worker() -> None:
            try:
                components = inspect_schematic_components(
                    path,
                    lambda current, total: self._progress(
                        4 + int(92 * current / max(1, total)),
                        "逐页辨识原理图元件",
                        f"第{current}/{total}页；正在建立RefDes、料号与实体Pin清单。",
                    ),
                )
                if components:
                    result: dict[str, object] = {
                        "ok": True,
                        "source_name": path.name,
                        "component_count": len(components),
                        "components": components,
                        "message": f"已识别{len(components)}个候选，请选择要建立的零件。",
                    }
                    self._progress(100, "原理图元件盘点完成", result["message"])
                else:
                    result = {
                        "ok": False,
                        "message": "原理图PDF中未找到可验证的RefDes与Pin标记；请确认这是EDA导出的可搜索PDF。",
                    }
                    self._progress(100, "原理图盘点HOLD", result["message"], "error")
            except Exception as exc:  # noqa: BLE001
                logging.exception("原理图元件后台盘点失败")
                result = {"ok": False, "message": f"无法读取原理图元件：{exc}"}
                self._progress(100, "原理图盘点失败", str(exc), "error")
            finally:
                with self._state_lock:
                    self._job_result = result
                    self._job_done = True
                self._build_lock.release()

        threading.Thread(target=worker, name=f"schematic-inventory-{job_id}", daemon=True).start()
        return {"ok": True, "job_id": job_id, "source_name": path.name}

    def start_variant_build(self, variant_index: int) -> dict[str, object]:
        """用户确认多封装Datasheet的实际Variant后继续同一通用流程。"""
        try:
            selected_index = int(variant_index)
        except (TypeError, ValueError):
            return {"ok": False, "message": "封装候选编号无效，请重新选择。"}
        pdf_path = self._pending_variant_pdf
        if pdf_path is None or not pdf_path.is_file():
            return {"ok": False, "message": "封装候选会话已失效，请重新导入Datasheet。"}
        if not self._build_lock.acquire(blocking=False):
            return {"ok": False, "busy": True, "message": "已有Datasheet任务正在执行。"}
        with self._state_lock:
            self._job_id += 1
            job_id = self._job_id
            self._job_done = False
            self._job_result = None
            self._job_progress = {
                "percent": 4,
                "stage": "已确认封装Variant",
                "detail": f"正在重新建立第{selected_index + 1}个候选的证据闭环。",
                "status": "",
            }

        def worker() -> None:
            try:
                result = self._build_datasheet(pdf_path, selected_index)
            except Exception as exc:  # noqa: BLE001
                logging.exception("封装Variant后台处理发生未捕获异常")
                self._progress(100, "处理失败", str(exc), "error")
                result = {"ok": False, "message": f"处理失败：{exc}"}
            finally:
                with self._state_lock:
                    self._job_result = result
                    self._job_done = True
                self._build_lock.release()

        threading.Thread(target=worker, name=f"variant-job-{job_id}", daemon=True).start()
        return {"ok": True, "job_id": job_id, "source_name": pdf_path.name}

    def job_status(self, job_id: int) -> dict[str, object]:
        with self._state_lock:
            if job_id != self._job_id:
                return {"ok": False, "done": True, "message": "任务编号已失效。"}
            return {
                "ok": True,
                "job_id": self._job_id,
                "done": self._job_done,
                "progress": dict(self._job_progress),
                "result": self._job_result if self._job_done else None,
            }

    def open_output_folder(self) -> dict[str, object]:
        if self._last_output is None or not self._last_output.exists():
            return {"ok": False, "message": "尚未生成输出。"}
        os.startfile(self._last_output)  # type: ignore[attr-defined]
        return {"ok": True}

    def open_in_orcad(self) -> dict[str, object]:
        target = self._last_orcad_target
        if target is None or not target.is_file():
            return {"ok": False, "message": "尚无通过实际回读Gate的OLB。"}
        capture, _source = _detect_capture_executable()
        if capture is None:
            return {"ok": False, "message": "未找到OrCAD Capture。"}
        subprocess.Popen([str(capture), str(target)], cwd=target.parent)  # noqa: S603
        return {"ok": True, "message": f"已用OrCAD Capture打开{target.name}"}

    def open_in_allegro(self) -> dict[str, object]:
        target = self._last_allegro_target
        if target is None or not target.exists():
            return {"ok": False, "message": "尚无通过门禁的DRA或SKILL，Allegro未调用。"}
        allegro = self._allegro_executable()
        if not allegro.exists():
            return {"ok": False, "message": "未找到Allegro PCB Editor。请单击顶部“选择Allegro”，指定allegro.exe。"}
        allegro_version = _allegro_version_from_path(allegro)
        if allegro_version == "未验证":
            return {"ok": False, "message": "无法读取Allegro执行文件版本；禁止按目录名称猜测版本，请重新选择完整安装的allegro.exe。"}
        try:
            from datasheet_to_allegro.cadence_compatibility import compatibility_error, session_failure, target_version_error
            if target.suffix.lower() == ".dra":
                mismatch = compatibility_error(target, allegro_version)
                if mismatch:
                    return {"ok": False, "message": mismatch, "target": str(target)}
            # Never terminate a review window: the engineer may have unsaved edits.
            native_psm: Path | None = None
            # 生成完成后，验证按钮必须打开实际DRA，不能再次执行IL。
            # PSM内容未变化时可能不会被Allegro重写，不能把PSM时间戳当作成功门槛。
            if target.suffix.lower() == ".il":
                replay = target.with_suffix(".scr")
                replay.write_text(
                    f'skill load("{target.resolve().as_posix()}")\nexit\nfillin no\n',
                    encoding="utf-8",
                )
                launch_file = target.with_suffix(".launch.json")
                launch = json.loads(launch_file.read_text(encoding="utf-8")) if launch_file.exists() else {}
                mismatch = target_version_error(allegro_version, launch.get("target_version"))
                if mismatch:
                    return {"ok": False, "message": mismatch}
                dra_name = str(launch.get("dra_name", ""))
                if not dra_name or Path(dra_name).name != dra_name or Path(dra_name).suffix.lower() != ".dra":
                    return {"ok": False, "message": "缺少有效的生成清單，禁止用目錄中任意DRA冒充本次結果。"}
                native_dra = target.parent / dra_name
                # Preserve previous staged artifacts for recovery; only the
                # exact manifest target can satisfy this generation request.
                if native_dra.exists() or native_dra.with_suffix(".psm").exists():
                    archive = Path(tempfile.mkdtemp(prefix="previous_generation_", dir=target.parent))
                    for old in (native_dra, native_dra.with_suffix(".psm")):
                        if old.exists():
                            old.rename(archive / old.name)
                if launch.get("mode") == "symbol_package":
                    native_psm = native_dra.with_suffix(".psm")
                    # The output is an isolated staging folder. A new symbol drawing
                    # is required so reruns never append duplicate pins to an old DRA.
                    args = [str(allegro), "-symbol", "package", str(native_dra), "-s", str(replay)]
                else:
                    args = [str(allegro)]
                    # Start with this installation's blank layout database.
                    # A bundled BRD saved by 17.4 cannot be opened by 17.2
                    # (SPMHOD-29), before the generator even runs.
                    args.extend(["-s", str(replay)])
            else:
                args = [str(allegro), str(target)]
            launched_at = time.time()
            session_log = target.parent / "allegro_generation_session.log"
            if target.suffix.lower() == ".il":
                with session_log.open("wb") as log_stream:
                    process = subprocess.Popen(args, cwd=target.parent, stdout=log_stream, stderr=subprocess.STDOUT)
            else:
                process = subprocess.Popen(args, cwd=target.parent)  # noqa: S603 - fixed trusted local executable
            if target.suffix.lower() != ".il":
                self._allegro_review_process = process
            if target.suffix.lower() == ".il":
                # Allegro startup time varies widely with license checkout,
                # antivirus scanning and 17.2/17.4 workstation performance.
                # Twenty seconds produced a false failure even though the DRA
                # appeared a few seconds later. Allow one minute and keep
                # checking the actual file timestamp.
                for step in range(60):
                    failure = session_failure(session_log.read_text(encoding="utf-8", errors="replace"))
                    if failure:
                        return {"ok": False, "message": failure, "session_log": str(session_log)}
                    artifacts = [
                        path for pattern in ("*.pad", "*.dra", "*.psm")
                        for path in target.parent.glob(pattern)
                        if path.stat().st_mtime >= launched_at - 1.0
                    ]
                    generated_dra = native_dra if native_dra.exists() and native_dra.stat().st_size > 0 and native_dra.stat().st_mtime >= launched_at - 1.0 else None
                    if generated_dra is not None:
                        names = "、".join(sorted(path.name for path in artifacts))
                        try:
                            process.wait(timeout=12)
                        except subprocess.TimeoutExpired:
                            return {
                                "ok": False,
                                "message": f"已生成Footprint：{generated_dra.name}；Allegro宿主窗口仍在关闭，请再次单击验证。",
                                "target": str(generated_dra),
                            }
                        if native_psm is not None and (not native_psm.exists() or native_psm.stat().st_size == 0):
                            return {
                                "ok": False,
                                "message": "Allegro已写入DRA，但原生Package Symbol编译未产生PSM；请查看同目录Create Symbol日志。",
                            }
                        from datasheet_to_allegro.cadence_compatibility import generated_version_error
                        completed_log = session_log.read_text(encoding="utf-8", errors="replace")
                        failure = session_failure(completed_log) or generated_version_error(generated_dra, allegro_version, completed_log)
                        if failure:
                            return {"ok": False, "message": failure, "session_log": str(session_log)}
                        if process.returncode != 0:
                            return {"ok": False, "message": f"Allegro異常退出({process.returncode})，即使已有DRA仍未通過驗證。", "session_log": str(session_log)}
                        self._last_allegro_target = generated_dra
                        (target.parent / "cadence_generation.json").write_text(json.dumps({
                            "executable": str(allegro), "version": allegro_version,
                            "host": "local_blank_database", "dra": str(generated_dra),
                            "session_log": str(session_log),
                        }, ensure_ascii=False, indent=2), encoding="utf-8")
                        artifacts = [
                            path for pattern in ("*.pad", "*.dra", "*.psm")
                            for path in target.parent.glob(pattern)
                            if path.stat().st_mtime >= launched_at - 1.0
                        ]
                        names = "、".join(sorted(path.name for path in artifacts))
                        show_replay = self._write_review_replay(generated_dra)
                        self._allegro_review_process = subprocess.Popen(
                            [str(allegro), str(generated_dra), "-s", str(show_replay)],
                            cwd=generated_dra.parent,
                        )  # noqa: S603
                        return {
                            "ok": True,
                            "message": f"Allegro {allegro_version}已生成并保持打开Footprint：{generated_dra.name}（{names}）",
                            "target": str(generated_dra),
                            "review_window_kept_open": True,
                        }
                    self._progress(92 + min(step // 9, 7), "Allegro正在生成", f"等待.pad/.dra落盘（{step + 1}/60）。")
                    if hasattr(process, "poll") and process.poll() is not None:
                        return {"ok": False, "message": "Allegro已退出但没有生成DRA，请查看allegro_generation_session.log。",
                                "session_log": str(session_log)}
                    time.sleep(1)
                return {
                    "ok": False,
                    "message": "Allegro已执行SKILL，但60秒内未检测到新的DRA；本次任务未误报为成功。请查看输出目录和Allegro命令窗口。",
                }
            return {"ok": True, "message": f"Allegro {allegro_version}已打开并保持显示Footprint：{target.name}", "target": str(target), "review_window_kept_open": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": f"Allegro启动失败：{exc}"}


def _configure_diagnostics() -> None:
    global _FAULT_LOG_HANDLE
    log_dir = output_root() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_dir / "application.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(threadName)s %(message)s",
        encoding="utf-8",
    )
    _FAULT_LOG_HANDLE = (log_dir / "fatal.log").open("a", encoding="utf-8")
    faulthandler.enable(_FAULT_LOG_HANDLE)


def _acquire_single_instance() -> bool:
    global _INSTANCE_MUTEX
    kernel32 = ctypes.windll.kernel32
    suffix = "Developer" if build_mode() == "developer" else "Portable"
    _INSTANCE_MUTEX = kernel32.CreateMutexW(None, False, f"Local\\Community_Footprint_Studio_{suffix}")
    return bool(_INSTANCE_MUTEX) and kernel32.GetLastError() != 183


def main() -> None:
    if '--self-test' in sys.argv:
        from datasheet_to_allegro.universal_extractor import _tesseract_executable, _pdftoppm_executable
        from datasheet_to_allegro.ocr_crosscheck import _engine
        target = Path(sys.argv[sys.argv.index('--self-test') + 1])
        result = {"version": __version__, "frozen": bool(getattr(sys, "frozen", False))}
        try:
            import pdfplumber
            import shapely
            result.update(extraction_mode="generic_evidence_parser", document_answer_registry=False)
            _engine()
            executable = _tesseract_executable()
            if executable is None or _pdftoppm_executable() is None:
                raise RuntimeError("Packaged OCR/renderer is missing")
            process = subprocess.run([str(executable), '--version'], capture_output=True, timeout=30)
            if process.returncode:
                raise RuntimeError(f"Tesseract failed: {process.returncode}")
            result.update(ok=True, tesseract=str(executable), poppler=str(_pdftoppm_executable()),
                          ocr_version=process.stdout.decode('utf-8', errors='replace').splitlines()[0])
        except Exception as exc:
            result.update(ok=False, error=str(exc))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        raise SystemExit(0 if result['ok'] else 1)
    if '--verify-footprint' in sys.argv:
        from datasheet_to_allegro.native_verification import main as verify_main
        raise SystemExit(verify_main(sys.argv[sys.argv.index('--verify-footprint') + 1:]))
    if '--review-connectivity' in sys.argv:
        from datasheet_to_allegro.connectivity_review import main as review_main
        raise SystemExit(review_main(sys.argv[1:]))
    if '--audit-capture' in sys.argv:
        from datasheet_to_allegro.capture_audit_entry import main as audit_main
        audit_main(sys.argv[1:], resource_root())
        return
    initialize_user_workspace()
    _configure_diagnostics()
    if not _acquire_single_instance():
        ctypes.windll.user32.MessageBoxW(None, "程序已经在运行，请切换到现有窗口。", "Community Footprint Intelligence", 0x40)
        return
    ui_dir = resource_root() / "ui"
    api = LandProofApi()
    title = "Community Footprint Intelligence Studio"
    if build_mode() == "developer":
        title += " · 开发测试版"
    window = webview.create_window(
        title,
        (ui_dir / "index.html").as_uri(),
        js_api=api,
        width=1280,
        height=800,
        min_size=(960, 640),
    )
    api._window = window
    logging.info("应用启动，资源目录=%s", resource_root())
    webview.start(debug=False, gui="edgechromium")


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        logging.exception("应用启动失败")
        try:
            ctypes.windll.user32.MessageBoxW(None, "程序启动失败，请查看Community_AI_Layout_Output\\logs\\application.log。", "Community Footprint Intelligence", 0x10)
        except Exception:
            pass
