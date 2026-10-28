"""Scenario loaders. Add a dataset adapter by implementing ``ScenarioLoader``
and registering it in ``LOADER_REGISTRY``."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import yaml

from ..errors import ConfigError
from .models import Scenario


class ScenarioLoader(Protocol):
    def load_all(self) -> list[Scenario]: ...


class YamlDirLoader:
    """Loads ``<root>/<scenario_id>/scenario.yaml`` records."""

    def __init__(self, path: str | Path, only: list[str] | None = None) -> None:
        self.root = Path(path)
        self.only = set(only) if only else None

    def load_all(self) -> list[Scenario]:
        if not self.root.is_dir():
            raise ConfigError(f"scenario root not found: {self.root}")
        out: list[Scenario] = []
        for d in sorted(self.root.iterdir()):
            f = d / "scenario.yaml"
            if not f.is_file():
                continue
            data = yaml.safe_load(f.read_text()) or {}
            data.setdefault("id", d.name)
            data["scenario_dir"] = d
            sc = Scenario.model_validate(data)
            if self.only and sc.id not in self.only:
                continue
            if not sc.sandbox_source.is_dir():
                raise ConfigError(f"scenario {sc.id}: sandbox_files dir missing: {sc.sandbox_source}")
            out.append(sc)
        if not out:
            raise ConfigError(f"no scenarios found under {self.root}")
        return out


LOADER_REGISTRY: dict[str, type] = {"yaml_dir": YamlDirLoader}


def get_loader(cfg: dict[str, Any]) -> ScenarioLoader:
    cfg = dict(cfg)
    kind = cfg.pop("loader", "yaml_dir")
    try:
        cls = LOADER_REGISTRY[kind]
    except KeyError as e:
        raise ConfigError(f"unknown scenario loader '{kind}'; known: {sorted(LOADER_REGISTRY)}") from e
    return cls(**cfg)
