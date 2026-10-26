"""Configuration loading: defaults < config file < environment < CLI flags."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import tomllib

DEFAULT_PROXY = "http://127.0.0.1:7897"
DEFAULT_CONFIG_NAME = "xdld.toml"
USER_CONFIG_DIR = Path.home() / ".config" / "xdld"

ENV_PREFIX = "XDLD_"

PATH_FIELDS = ("cookies", "output_dir", "db")
# Fields that hold a size and accept "50MB" as well as a plain byte count.
SIZE_FIELDS = ("max_size",)

_SIZE_UNITS = {
    "": 1,
    "B": 1,
    "K": 1024,
    "KB": 1024,
    "KIB": 1024,
    "M": 1024**2,
    "MB": 1024**2,
    "MIB": 1024**2,
    "G": 1024**3,
    "GB": 1024**3,
    "GIB": 1024**3,
    "T": 1024**4,
    "TB": 1024**4,
    "TIB": 1024**4,
}

_SIZE_RE = re.compile(r"^([0-9]*\.?[0-9]+)\s*([a-z]*)$", re.IGNORECASE)


def parse_size(value: Any) -> int:
    """Read a size as a number of bytes.

    ``50MB``, ``1.5 GB``, ``800K`` and ``50000000`` are all understood.
    ``0`` means "no limit".
    """
    if isinstance(value, bool):
        raise ValueError(f"cannot read the size {value!r}")
    if isinstance(value, (int, float)):
        return int(value)
    match = _SIZE_RE.match(str(value).strip().replace(",", ""))
    if not match:
        raise ValueError(
            f"cannot read the size {value!r} (try 50MB, 1.5GB or 8000000)"
        )
    number, unit = match.group(1), match.group(2).upper()
    if unit not in _SIZE_UNITS:
        raise ValueError(
            f"unknown size unit {unit!r} (use B, KB, MB, GB or TB)"
        )
    return int(float(number) * _SIZE_UNITS[unit])


@dataclass
class Config:
    """Every knob the tool exposes."""

    # --- network ---
    proxy: str = DEFAULT_PROXY
    language: str = "en-US"
    impersonate: str = ""

    # --- auth ---
    cookies: str = "cookies.txt"

    # --- storage ---
    output_dir: str = "downloads"
    db: str = "xdld.db"

    # --- crawl ---
    max_likes: int = 0  # 0 == unlimited
    page_size: int = 40
    sleep_min: float = 1.0
    sleep_max: float = 2.5
    # Stop once this many already-known tweets were seen back to back.
    stop_after_known: int = 50
    # Start at this page of the likes timeline (1 == the newest page).
    from_page: int = 1

    # --- output ---
    download_images: bool = False
    overwrite: bool = False
    filename_template: str = "{tweet_id}_{user}_{index}{ext}"
    write_index: bool = True

    # --- limits ---
    # The biggest file we are willing to download (in bytes).  0 == no
    # limit: the highest resolution variant is taken whatever its size is.
    max_size: int = 0
    # What to do when not even the smallest variant fits into ``max_size``:
    # False (default) -> download the smallest one anyway, a video is not
    # thrown away because of its size.  True -> skip the video, the limit
    # is a hard rule then.
    max_size_strict: bool = False
    # Whether the real size of a variant is measured (one small request per
    # variant, the answer is cached in the database).  When it is off the
    # size is estimated from the bitrate, which is about twice too big - the
    # limit is then roughly half as big as it looks.
    max_size_probe: bool = True

    def resolved(self, base: Path) -> Config:
        """Turn relative paths into absolute ones, relative to ``base``."""
        base = base.resolve()
        for name in ("cookies", "output_dir", "db"):
            value = getattr(self, name)
            if value and not os.path.isabs(value):
                setattr(self, name, str((base / value).resolve()))
        return self


def _coerce(field_type: Any, raw: str) -> Any:
    # ``from __future__ import annotations`` turns the annotations of the
    # dataclass into strings, so both spellings have to be accepted.
    if field_type in (bool, "bool"):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if field_type in (int, "int"):
        return int(raw)
    if field_type in (float, "float"):
        return float(raw)
    return raw


def _convert(name: str, value: Any) -> Any:
    """Turn a raw value into the type the config field expects."""
    if name in SIZE_FIELDS:
        return parse_size(value)
    return value


def _read_config_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    known = {f.name for f in fields(Config)}
    return {k: v for k, v in data.items() if k in known}


def load_config(
    config_path: str | None = None,
    base_dir: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> Config:
    """Build a :class:`Config` from every supported source.

    Paths that come from a config file are resolved against the directory of
    that file; paths given on the command line (or via the environment) are
    resolved against the current working directory.
    """
    cwd = Path(base_dir or Path.cwd()).resolve()
    cfg = Config()

    # 1. config file
    candidates: list[Path] = []
    if config_path:
        candidates.append(Path(config_path))
    else:
        candidates.append(cwd / DEFAULT_CONFIG_NAME)
        candidates.append(USER_CONFIG_DIR / DEFAULT_CONFIG_NAME)
        # When the tool runs from a checkout (editable install), the config
        # next to the source tree is used even from another directory.
        repo_root = Path(__file__).resolve().parent.parent
        if (repo_root / "xdld" / "config.py").is_file():
            candidates.append(repo_root / DEFAULT_CONFIG_NAME)
    for candidate in candidates:
        if candidate.is_file():
            for key, value in _read_config_file(candidate).items():
                setattr(cfg, key, _convert(key, value))
            # Relative paths in the file belong next to the file.
            cfg.resolved(candidate.parent)
            break
    else:
        # No config file: relative paths belong to the working directory.
        cfg.resolved(cwd)

    # 2. environment
    for f in fields(Config):
        env_key = ENV_PREFIX + f.name.upper()
        if env_key in os.environ:
            raw = os.environ[env_key]
            if f.name in SIZE_FIELDS:
                value = parse_size(raw)
            else:
                value = _coerce(f.type, raw)
                if f.name in PATH_FIELDS and value:
                    value = str((cwd / value).resolve())
            setattr(cfg, f.name, value)

    # 3. explicit CLI overrides
    for key, value in (overrides or {}).items():
        if value is None:
            continue
        if key in SIZE_FIELDS:
            value = parse_size(value)
        elif key in PATH_FIELDS and value:
            value = str((cwd / value).resolve())
        setattr(cfg, key, value)

    return cfg
