import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import sys
import time
import tomllib
import unicodedata
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# --- Platform Setup ---


def enable_ansi_utf8() -> None:
    """Enable ANSI escape sequences and UTF-8 output on Windows consoles."""
    if sys.platform != "win32":
        return
    os.system("")
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


# --- Formatting ---


def bool_str(value: Any) -> str:
    """Convert a truthy/falsy value to '1' or '0' for INI files."""
    return "1" if value else "0"


def sanitize_filename(filename: str, replacement_char: str = "") -> str:
    """Sanitize a string for use as a cross-platform valid filename."""
    if not filename:
        return "unnamed"

    filename = unicodedata.normalize("NFC", filename)
    filename = re.sub(r'[<>:"/\\|?*\x00]', replacement_char, filename)
    ignore_prefixes = ("C", "S")
    filename = "".join(c for c in filename if not any(unicodedata.category(c).startswith(p) for p in ignore_prefixes))
    filename = re.sub(r"\s+", " ", filename)
    filename = filename.strip().rstrip(". ")
    reserved_pattern = r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?$"
    if re.match(reserved_pattern, filename, re.IGNORECASE):
        filename = f"{replacement_char}{filename}"

    if not filename:
        return "unnamed"

    return filename[:250]


# --- Data ---


def deep_merge(base: dict[str, Any], update: dict[str, Any]) -> None:
    """Recursively merge update into base, mutating base in place."""
    for key, value in update.items():
        if isinstance(value, dict) and key in base and isinstance(base[key], dict):
            deep_merge(base[key], value)
        else:
            base[key] = value


# --- File I/O ---


def read_toml(path: str | Path | None, default: Any = None) -> Any:
    """Load data from a TOML file with specified fallback."""
    fallback = {} if default is None else default
    if not path:
        return fallback

    p = Path(path)
    if not p.exists():
        return fallback

    try:
        with p.open("rb") as f:
            return tomllib.load(f)
    except Exception as e:
        logger.error(f"Failed to read TOML from {path}: {e}")

    return fallback


def read_json(path: str | Path | None, default: Any = None) -> Any:
    """Load data from a JSON file with specified fallback."""
    fallback = {} if default is None else default
    if not path:
        return fallback

    p = Path(path)
    if not p.exists():
        return fallback

    try:
        with p.open("r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to read JSON from {path}: {e}")

    return fallback


def write_json(path: str | Path | None, data: Any, indent: int = 4) -> bool:
    """Safely write JSON data atomically to a file, ensuring parent directories exist."""
    if not path:
        return False

    p = Path(path)
    temp_p = p.with_suffix(p.suffix + ".tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with temp_p.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        temp_p.replace(p)
        return True
    except Exception as e:
        logger.error(f"Failed to write JSON to {path}: {e}")
        if temp_p.exists():
            with contextlib.suppress(Exception):
                temp_p.unlink()

    return False


def write_ini(path: Path, lines: list[str]) -> None:
    """Write INI lines with CRLF endings, dropping bare '=' and key-less non-section lines."""
    validated_lines = []
    for line in lines:
        line_str = str(line).strip()
        if line_str == "=" or (
            not line_str.startswith("[")
            and not line_str.startswith("###")
            and not line_str.startswith("#")
            and "=" not in line_str
            and line_str != ""
        ):
            continue
        validated_lines.append(line_str)

    with open(path, "wb") as f:
        f.write(("\r\n".join(validated_lines) + "\r\n").encode("utf-8"))


# --- File Operations ---


def force_writable(path: str | Path | None) -> None:
    """Attempt to set file permissions to writable."""
    if not path:
        return

    p = Path(path)
    if p.exists():
        with contextlib.suppress(Exception):
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)


def backup_file(path: str | Path | None, suffix: str = ".bak", remove_original: bool = True) -> Path | None:
    """Create a backup of a file if it doesn't already exist, with retries and permissions reset."""
    if not path:
        return None

    p = Path(path)
    if not p.exists():
        return None

    backup_p = p.with_suffix(p.suffix + suffix)
    if backup_p.exists():
        logger.debug(f"Backup already exists: {backup_p}")
        return backup_p

    max_retries = 5
    delay = 0.1
    for attempt in range(max_retries):
        try:
            force_writable(backup_p)
            shutil.copyfile(str(p), str(backup_p))

            if remove_original:
                force_writable(p)
                p.unlink()
            logger.info(f"Created backup: {backup_p.name}")
            return backup_p
        except (PermissionError, OSError) as e:
            if attempt == max_retries - 1:
                logger.error(f"Backup failed for {path} after {max_retries} attempts: {e}")
                return None
            time.sleep(delay)
        except Exception as e:
            logger.error(f"Backup failed for {path}: {e}")
            return None

    return None


def copy_file(src_path: str | Path, dst_path: str | Path, max_retries: int = 5, delay: float = 0.1) -> bool:
    """Copy file with retries and force-permission overrides."""
    src_p = Path(src_path)
    dst_p = Path(dst_path)
    for attempt in range(max_retries):
        try:
            force_writable(dst_p)
            shutil.copyfile(str(src_p), str(dst_p))
            return True
        except (PermissionError, OSError):
            force_writable(dst_p)
            if attempt == max_retries - 1:
                raise
            time.sleep(delay)
        except Exception:
            if attempt == max_retries - 1:
                raise
            time.sleep(delay)

    return False


def copy_file_with_backup(src_path: str | Path, dst_dir: str | Path) -> list[str]:
    """Copy a single file into the destination, backing up if it already exists."""
    src_p = Path(src_path)
    dst_p = Path(dst_dir)
    logger.info(f"Copying file: {src_p.name} to {dst_p.resolve()}")

    dst_file_p = dst_p / src_p.name
    tracking_files = [str(dst_file_p.resolve())]
    backup_p = backup_file(dst_file_p)
    if backup_p:
        tracking_files.append(str(backup_p.resolve()))
    copy_file(src_p, dst_file_p)

    return tracking_files


def copy_dir_with_backup(src_dir: str | Path, dst_dir: str | Path) -> list[str]:
    """Recursively copy a directory tree into destination, backing up pre-existing files."""
    src_p = Path(src_dir)
    dst_p = Path(dst_dir)
    logger.info(f"Copying directory: {src_p.name} to {dst_p.resolve()}")

    tracking_files: list[str] = []
    for item_p in src_p.rglob("*"):
        if item_p.is_file():
            rel_path_p = item_p.relative_to(src_p)
            dst_subdir_p = dst_p / rel_path_p.parent
            dst_subdir_p.mkdir(parents=True, exist_ok=True)
            tracking_files.extend(copy_file_with_backup(item_p, dst_subdir_p))

    return tracking_files


def copy_with_backup(src: str | Path, dst_dir: str | Path) -> list[str]:
    """Copy a file or directory tree into destination, backing up pre-existing files."""
    src_p = Path(src)
    if src_p.is_dir():
        return copy_dir_with_backup(src_p, dst_dir)

    return copy_file_with_backup(src_p, dst_dir)


def calculate_sha256(path: str | Path) -> str:
    """Calculate the SHA256 hash of a file."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    sha256_hash = hashlib.sha256()
    with p.open("rb") as f:
        for byte_block in iter(lambda: f.read(65536), b""):
            sha256_hash.update(byte_block)

    return sha256_hash.hexdigest()
