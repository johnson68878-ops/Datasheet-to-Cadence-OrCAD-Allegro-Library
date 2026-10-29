"""Read Cadence's saved-version marker and classify local session failures."""
import re
import ctypes
import os
from pathlib import Path


def target_version_error(actual: str | None, required: str | None = None) -> str | None:
    """One exact-version policy shared by desktop and unattended verification."""
    if not actual or not re.fullmatch(r"\d+\.\d+", actual):
        return "无法读取Allegro执行文件版本；禁止按目录名称猜测版本。"
    if required is not None and (not re.fullmatch(r"\d+\.\d+", required) or actual != required):
        return f"要求Allegro {required}，实际执行文件为{actual}；禁止以其他版本冒充目标版本。"
    return None


def executable_version(path: Path) -> str | None:
    """Read the PE version resource, not a user-renamable installation folder."""
    if os.name != "nt" or not path.is_file():
        return None
    try:
        version = ctypes.windll.version
        size = version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(path), 0, size, data):
            return None
        pointer, length = ctypes.c_void_p(), ctypes.c_uint()
        if not version.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)) or length.value < 52:
            return None
        fields = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
        major, minor = fields[4] >> 16, fields[4] & 65535
        if 10 <= major < 100 and minor <= 99:
            return f"{major}.{minor // 10 if minor >= 10 and minor % 10 == 0 else minor}"
    except (OSError, ValueError):
        pass
    return None


def session_version(log: str) -> str | None:
    matches = set(re.findall(r"Allegro[^\r\n]{0,100}?\b(\d+\.\d+)\s+P\d+", log, re.I))
    return next(iter(matches)) if len(matches) == 1 else None


def generated_version_error(path: Path, selected_version: str, log: str) -> str | None:
    runtime = session_version(log)
    if runtime and runtime != selected_version:
        return f"實際Allegro會話為{runtime}，選定版本為{selected_version}；禁止宣稱目標版本相容。"
    saved = saved_database_version(path)
    if saved:
        expected = re.fullmatch(r"(\d+)\.(\d+)", selected_version)
        if not expected or saved != tuple(map(int, expected.groups())):
            return f"輸出資料庫版本{saved[0]}.{saved[1]}不等於目標{selected_version}，未通過版本驗證。"
        return None
    if runtime != selected_version:
        return "輸出資料庫與會話日誌均未能證明目標版本；相容性保持未驗證。"
    return None


def saved_database_version(path: Path) -> tuple[int, int] | None:
    if path.suffix.lower() not in {".brd", ".dra", ".psm", ".pad"}:
        return None
    data = path.read_bytes()
    marker = data.rfind(b"ALLEGRO_DESIGN_WAS_LAST_SAVED")
    if marker < 0:
        return None
    match = re.search(rb"allegro\s+(\d+)\.(\d+)", data[marker:marker + 2048], re.I)
    return (int(match[1]), int(match[2])) if match else None


def compatibility_error(path: Path, selected_version: str) -> str | None:
    match = re.fullmatch(r"(\d+)\.(\d+)", selected_version)
    if not match:
        return "無法確認選定Allegro版本；不能將資料庫標記為相容。"
    saved = saved_database_version(path)
    if saved is None:
        return f"{path.name}的資料庫版本無法辨識；請由目標版本重新生成並回讀驗證。"
    if saved and saved > (int(match[1]), int(match[2])):
        return (f"{path.name}由Allegro {saved[0]}.{saved[1]}保存，当前选择{selected_version}，"
                "存在SPMHOD-29版本不兼容。请用当前工作站版本重新生成封装；不能直接打开较新版DRA。")
    return None


def session_failure(log: str) -> str | None:
    if re.search(r"SPMHOD-29|saved under a newer release", log, re.I):
        return "SPMHOD-29：输入数据库由较新版本保存。请使用当前工作站重新生成，并检查是否打开了旧输出或外部模板。"
    if re.search(r"SPMHOD-39|No Password was provided|Open\s+Cancel(?:ed|led)", log, re.I):
        return "Allegro未能打开输入数据库；请检查密码或取消打开的原因。"
    if re.search(r"\*Error\*|Unable to open design|Failed to (?:obtain|check out).*license|No licenses available", log, re.I):
        return "Allegro会话报告错误；请查看allegro_generation_session.log中的具体原因。"
    return None
