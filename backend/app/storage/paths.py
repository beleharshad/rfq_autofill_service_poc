"""Stable storage path helpers.

These helpers keep persisted artifacts rooted to the backend directory instead
of the process current working directory.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List


def _backend_root() -> Path:
	return Path(__file__).resolve().parents[2]


def _repo_root() -> Path:
	return _backend_root().parent


def _resolve_path(value: str | Path) -> Path:
	return Path(value).expanduser().resolve()


def data_root() -> Path:
	env_value = os.environ.get("RFQ_DATA_DIR")
	if env_value:
		return _resolve_path(env_value)
	return (_backend_root() / "data").resolve()


def jobs_root() -> Path:
	env_value = os.environ.get("RFQ_JOBS_DIR")
	if env_value:
		return _resolve_path(env_value)
	return (data_root() / "jobs").resolve()


def jobs_db_path() -> Path:
	env_value = os.environ.get("RFQ_JOBS_DB_PATH")
	if env_value:
		return _resolve_path(env_value)
	return (data_root() / "jobs.db").resolve()


def legacy_jobs_roots() -> List[Path]:
	primary = jobs_root()
	candidates = [(_repo_root() / "data" / "jobs").resolve()]
	return [candidate for candidate in candidates if candidate != primary]


def legacy_jobs_db_paths() -> List[Path]:
	primary = jobs_db_path()
	candidates = [(_repo_root() / "data" / "jobs.db").resolve()]
	return [candidate for candidate in candidates if candidate != primary]
