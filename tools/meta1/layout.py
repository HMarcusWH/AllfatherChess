"""Producer-filesystem identity for relocation-safe META-1 verification."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from tools.local_game.common import require


def _absolute_clean(value: Any, label: str) -> Path:
    require(isinstance(value, str) and value, f"{label} must be a non-empty string")
    require("\x00" not in value, f"{label} contains a NUL byte")
    path = Path(value)
    require(path.is_absolute(), f"{label} must be absolute")
    require(".." not in path.parts, f"{label} may not contain parent traversal")
    return path


@dataclass(frozen=True)
class ProducerLayout:
    repo_root: str
    campaign_root: str
    fastchess: str
    python_executable: str

    @classmethod
    def create(
        cls,
        *,
        repo_root: Path | str,
        campaign_root: Path | str,
        fastchess: Path | str,
        python_executable: str,
        campaign_id: str,
    ) -> "ProducerLayout":
        return cls(
            repo_root=str(repo_root),
            campaign_root=str(campaign_root),
            fastchess=str(fastchess),
            python_executable=str(python_executable),
        ).validate(campaign_id)

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any] | None,
        *,
        campaign_id: str,
    ) -> "ProducerLayout":
        require(isinstance(raw, Mapping), "META-1 producer_layout is missing")
        require(
            set(raw) == {
                "repo_root",
                "campaign_root",
                "fastchess",
                "python_executable",
            },
            "META-1 producer_layout keys drift",
        )
        return cls(
            repo_root=str(raw["repo_root"]),
            campaign_root=str(raw["campaign_root"]),
            fastchess=str(raw["fastchess"]),
            python_executable=str(raw["python_executable"]),
        ).validate(campaign_id)

    def validate(self, campaign_id: str) -> "ProducerLayout":
        require(
            isinstance(campaign_id, str) and campaign_id and "/" not in campaign_id,
            "META-1 campaign id is malformed",
        )
        repo = _absolute_clean(self.repo_root, "producer repo_root")
        campaign = _absolute_clean(self.campaign_root, "producer campaign_root")
        fastchess = _absolute_clean(self.fastchess, "producer fastchess")
        _absolute_clean(self.python_executable, "producer python_executable")
        require(
            campaign == repo / "build/test-results/meta1" / campaign_id,
            "producer campaign_root is outside the canonical META-1 build path",
        )
        require(
            fastchess == repo / "build/tools/fastchess/bin/fastchess",
            "producer Fastchess path differs from the pinned build location",
        )
        return self

    def as_dict(self) -> dict[str, str]:
        return {
            "repo_root": self.repo_root,
            "campaign_root": self.campaign_root,
            "fastchess": self.fastchess,
            "python_executable": self.python_executable,
        }

    def block_directory(self, block_id: str) -> Path:
        require(
            isinstance(block_id, str) and block_id and "/" not in block_id,
            "META-1 block id is malformed",
        )
        return Path(self.campaign_root) / block_id
