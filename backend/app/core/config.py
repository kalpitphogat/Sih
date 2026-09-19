"""Application configuration.

Engineering rule 5: everything is a config, not a constant. Anything that could
conceivably differ between machines or scenarios is read from the environment or
a scenario YAML, never hardcoded in a module.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env"),
        env_prefix="",
        extra="ignore",
    )

    app_name: str = "FloodGuard India"
    version: str = "0.1.0"

    floodguard_data_dir: Path = REPO_ROOT / "data"
    floodguard_log_level: str = "INFO"

    # Optional external credentials. Absent is a valid, handled state.
    opentopo_api_key: str = ""
    google_application_credentials: str = ""

    # Optional external engine binaries. Discovered on PATH if unset.
    dflowfm_bin: str = ""
    dimr_bin: str = ""
    dualsphysics_bin: str = ""
    gencase_bin: str = ""

    @property
    def raw_dir(self) -> Path:
        return self.floodguard_data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.floodguard_data_dir / "processed"

    @property
    def scenarios_dir(self) -> Path:
        return self.floodguard_data_dir / "scenarios"

    @property
    def catalog_dir(self) -> Path:
        return self.floodguard_data_dir / "catalog"

    @property
    def manifest_path(self) -> Path:
        return self.floodguard_data_dir / "MANIFEST.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
