"""Configuration loading: defaults < config file < environment < CLI flags."""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import tomllib

DEFAULT_PROXY = "http://127.0.0.1:7897"
DEFAULT_CONFIG_NAME = "xdld.toml"
USER_CONFIG_DIR = Path.home() / ".config" / "xdld"

ENV_PREFIX = "XDLD_"

PATH_FIELDS = ("cookies", "output_dir", "db")


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

    def resolved(self, base: Path) -> Config:
        """Turn relative paths into absolute ones, relative to ``base``."""
        base = base.resolve()
        for name in ("cookies", "output_dir", "db"):
            value = getattr(self, name)
            if value and not os.path.isabs(value):
                setattr(self, name, str((base / value).resolve()))
        return self


def _coerce(field_type: Any, raw: str) -> Any:
    if field_type is bool:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if field_type is int:
        return int(raw)
    if field_type is float:
        return float(raw)
    return raw


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
                setattr(cfg, key, value)
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
            value = _coerce(f.type, os.environ[env_key])
            if f.name in PATH_FIELDS and value:
                value = str((cwd / value).resolve())
            setattr(cfg, f.name, value)

    # 3. explicit CLI overrides
    for key, value in (overrides or {}).items():
        if value is None:
            continue
        if key in PATH_FIELDS and value:
            value = str((cwd / value).resolve())
        setattr(cfg, key, value)

    return cfg
