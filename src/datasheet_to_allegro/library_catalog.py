from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class LibraryArtifact(BaseModel):
    file: str
    sha256: str
    bytes: int


class FootprintCandidate(BaseModel):
    symbol_name: str
    family: str
    component_kind: str
    source_board: str
    source_board_sha256: str
    dra: LibraryArtifact
    psm: LibraryArtifact | None = None
    padstacks: list[LibraryArtifact] = Field(default_factory=list)
    inferred_attributes: dict[str, object] = Field(default_factory=dict)
    verification_status: Literal["discovered", "datasheet_matched", "engineer_approved"] = "discovered"
    reuse_allowed: bool = False
    warnings: list[str] = Field(default_factory=list)


class ReferenceLibraryCatalog(BaseModel):
    schema_version: str = "0.2.0"
    source_board: str
    source_board_sha256: str
    generation_note: str = "Catalog entries are candidates only; filename inference is not engineering approval."
    footprints: list[FootprintCandidate]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _artifact(path: Path) -> LibraryArtifact:
    return LibraryArtifact(file=path.name, sha256=_sha256(path), bytes=path.stat().st_size)


def _strings(path: Path) -> list[str]:
    return re.findall(r"[ -~]{4,}", path.read_bytes().decode("latin1", errors="ignore"))


def _classify(name: str) -> tuple[str, str]:
    key = name.lower()
    patterns = (
        (r"^r(?:01005|0201|0402)", "CHIP_RESISTOR", "R"),
        (r"^c(?:01005|0201|0402)", "CHIP_CAPACITOR", "C"),
        (r"^l(?:0201|0805|2012)", "CHIP_INDUCTOR", "L"),
        (r"^(dfn|x1-dfn|ldo_dfn)", "DFN", "IC"),
        (r"^wlcsp", "WLCSP", "IC"),
        (r"^lga", "LGA", "IC"),
        (r"^mic", "MEMS", "IC"),
        (r"^(xtal|x1-)", "CRYSTAL", "XTAL"),
        (r"^led", "LED", "LED"),
        (r"^ant", "ANTENNA", "ANT"),
        (r"^con", "CONNECTOR", "CON"),
        (r"^test_", "TEST_POINT", "TP"),
    )
    for expression, family, kind in patterns:
        if re.search(expression, key):
            return family, kind
    return "OTHER", "UNKNOWN"


def _infer_name_attributes(name: str) -> dict[str, object]:
    key = name.lower()
    result: dict[str, object] = {}
    pin = re.search(r"(?:_|^)(\d+)(?:p|-\d+p)", key)
    pitch = re.search(r"_(\d+d\d+|\d+_\d+)(?:_|-)", key)
    body = re.search(r"_(\d+d\d+)x(\d+d\d+)", key)
    height = re.search(r"_h(\d+d\d+)", key)
    if pin:
        result["pin_count_inferred"] = int(pin.group(1))
    if pitch:
        result["pitch_mm_inferred"] = float(pitch.group(1).replace("d", ".").replace("_", "."))
    if body:
        result["body_mm_inferred"] = [float(body.group(1).replace("d", ".")), float(body.group(2).replace("d", "."))]
    if height:
        result["height_mm_inferred"] = float(height.group(1).replace("d", "."))
    return result


def build_reference_catalog(board_path: str | Path, dump_dir: str | Path) -> ReferenceLibraryCatalog:
    board, dump = Path(board_path), Path(dump_dir)
    if not board.is_file():
        raise FileNotFoundError(board)
    if not dump.is_dir():
        raise FileNotFoundError(dump)
    board_hash = _sha256(board)
    footprints: list[FootprintCandidate] = []
    for dra in sorted(dump.glob("*.dra")):
        symbol = dra.stem
        family, kind = _classify(symbol)
        psm_path = dump / f"{symbol}.psm"
        pad_names: set[str] = set()
        for raw in _strings(dra):
            for match in re.findall(r"([^\\/:]+\.pad)", raw, re.IGNORECASE):
                pad_names.add(Path(match).name.lower())
        pads = [_artifact(dump / name) for name in sorted(pad_names) if (dump / name).is_file()]
        warnings = ["尺寸由命名规则推断，必须与目标 Datasheet 逐项核对。"]
        if not pads:
            warnings.append("未从 DRA 字符串中识别到 Padstack 依赖。")
        footprints.append(FootprintCandidate(
            symbol_name=symbol,
            family=family,
            component_kind=kind,
            source_board=board.name,
            source_board_sha256=board_hash,
            dra=_artifact(dra),
            psm=_artifact(psm_path) if psm_path.is_file() else None,
            padstacks=pads,
            inferred_attributes=_infer_name_attributes(symbol),
            warnings=warnings,
        ))
    return ReferenceLibraryCatalog(source_board=board.name, source_board_sha256=board_hash, footprints=footprints)


def write_reference_catalog(board_path: str | Path, dump_dir: str | Path, output: str | Path) -> Path:
    catalog = build_reference_catalog(board_path, dump_dir)
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(catalog.model_dump_json(indent=2), encoding="utf-8")
    return target

