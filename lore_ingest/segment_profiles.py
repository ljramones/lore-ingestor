# lore_ingest/segment_profiles.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Pattern, Dict
import re


@dataclass(frozen=True)
class SceneRules:
    break_on_blank: bool = True
    heading_regex: Optional[Pattern[str]] = None
    min_scene_chars: int = 40
    max_scene_chars: int = 100_000
    heading_consumes_line: bool = False  # NEW: start scene *after* heading line


@dataclass(frozen=True)
class ChunkRules:
    window_chars: int = 512
    stride_chars: int = 384
    snap_to_sentence: bool = False


@dataclass(frozen=True)
class Profile:
    name: str
    scene: SceneRules
    chunk: ChunkRules


def _re(pat: str) -> re.Pattern[str]:
    return re.compile(pat, re.M | re.U)


PROFILES: Dict[str, Profile] = {
    "default": Profile(
        name="default",
        scene=SceneRules(break_on_blank=True, heading_regex=None, min_scene_chars=40),
        chunk=ChunkRules(window_chars=512, stride_chars=384),
    ),
    "dense": Profile(
        name="dense",
        scene=SceneRules(break_on_blank=True, heading_regex=None, min_scene_chars=20),
        chunk=ChunkRules(window_chars=384, stride_chars=256),
    ),
    "sparse": Profile(
        name="sparse",
        scene=SceneRules(break_on_blank=True, heading_regex=None, min_scene_chars=80),
        chunk=ChunkRules(window_chars=1024, stride_chars=768),
    ),
    "markdown": Profile(
        name="markdown",
        scene=SceneRules(break_on_blank=False, heading_regex=_re(r"^\s*#{1,6}\s+.+$")),
        chunk=ChunkRules(window_chars=512, stride_chars=384),
    ),
    "screenplay": Profile(
        name="screenplay",
        scene=SceneRules(
            break_on_blank=True,
            heading_regex=_re(r"^\s*(INT\.|EXT\.)\s+.+$"),
            min_scene_chars=20,
        ),
        chunk=ChunkRules(window_chars=512, stride_chars=384),
    ),
    # Split on the sentinel line we inserted: [[PAGE_BREAK]]
    "pdf_pages": Profile(
        name="pdf_pages",
        scene=SceneRules(
            break_on_blank=False,
            heading_regex=_re(r"^\s*\[\[PAGE_BREAK\]\]\s*$"),
            min_scene_chars=120,   # avoid super tiny pages
            heading_consumes_line=True,  # do not include the marker in any scene
        ),
        chunk=ChunkRules(window_chars=512, stride_chars=384),
    ),
}


def get_profile(name: str | None) -> Profile:
    if not name:
        return PROFILES["default"]
    return PROFILES.get(name.lower(), PROFILES["default"])
