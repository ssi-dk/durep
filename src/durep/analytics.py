from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

EXTENSIONS = {
    "fasta": ["fna", "faa", "fasta", "fa"],
    "fastq": ["fastq", "fq"],
    "sam": ["sam"],
    "vcf": ["vcf"],
}


# Users might want information about data that is taking up more space
# that necessary. These formats are common in bioinformatics, large, and
# typically compressed, so seeing them uncompressed is a sign of waste.
@dataclass(slots=True)
class UncompressedStats:
    fasta: int
    fastq: int
    vcf: int
    sam: int


# Metrics reported for one project or user
@dataclass(slots=True)
class GlobalMetrics:
    total_usage_bytes: int
    total_files: int
    total_uncompressed: UncompressedStats


# Similar to NcduNode, but this stores the information
# after filtering/pruning etc; the data we want to render.
@dataclass(slots=True)
class DrilldownNode:
    path: Path
    node_type: Literal["file", "dir"]
    total_bytes: int
    uncompressed: UncompressedStats
    children: list[DrilldownNode] = field(default_factory=list)


# If a path occurs in NCDUs at different times, this class is used to store
# whether data under that path grew or shrunk
@dataclass(slots=True)
class PathDelta:
    path: Path
    current_bytes: int
    previous_bytes: int

    @property
    def delta_bytes(self) -> int:
        return self.current_bytes - self.previous_bytes
