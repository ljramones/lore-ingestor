# lore_ingest/segment_profiles.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Pattern, Dict, List
import re


@dataclass(frozen=True)
class SceneRules:
    break_on_blank: bool = True
    heading_regex: Optional[Pattern[str]] = None
    min_scene_chars: int = 40
    max_scene_chars: int = 100_000
    heading_consumes_line: bool = False
    extra_split_regexes: List[Pattern[str]] = None  # type: ignore
    ignore_fenced_code: bool = False
    fence_open_regex: Optional[Pattern[str]] = None
    fence_close_regex: Optional[Pattern[str]] = None


@dataclass(frozen=True)
class ChunkRules:
    window_chars: int = 512
    stride_chars: int = 384
    snap_to_sentence: bool = False  # reserved


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
        scene=SceneRules(
            break_on_blank=True,
            heading_regex=None,
            min_scene_chars=40,
            extra_split_regexes=[],
        ),
        chunk=ChunkRules(window_chars=512, stride_chars=384),
    ),

    "dense": Profile(
        name="dense",
        scene=SceneRules(
            break_on_blank=True,
            heading_regex=None,
            min_scene_chars=20,
            extra_split_regexes=[],
        ),
        chunk=ChunkRules(window_chars=384, stride_chars=256),
    ),

    "sparse": Profile(
        name="sparse",
        scene=SceneRules(
            break_on_blank=True,
            heading_regex=None,
            min_scene_chars=80,
            extra_split_regexes=[],
        ),
        chunk=ChunkRules(window_chars=1024, stride_chars=768),
    ),

    # Markdown: split on headings (# ...), but DO NOT split inside fenced code blocks
    # Lower min_scene_chars to avoid filtering short post-fence sections.
    "markdown": Profile(
        name="markdown",
        scene=SceneRules(
            break_on_blank=False,
            heading_regex=_re(r"^\s*#{1,6}\s+.+$"),
            min_scene_chars=1,                   # <-- was 40; allow short scenes after fences
            heading_consumes_line=False,
            extra_split_regexes=[],
            ignore_fenced_code=True,
            fence_open_regex=_re(r"^\s*(```|~~~)"),
            fence_close_regex=_re(r"^\s*(```|~~~)\s*$"),
        ),
        chunk=ChunkRules(window_chars=512, stride_chars=384),
    ),

    # Screenplay: sluglines (INT./EXT./EST./INT/EXT) + character cues & transitions
    # Lower min_scene_chars so brief cue lines form their own scene.
    "screenplay": Profile(
        name="screenplay",
        scene=SceneRules(
            break_on_blank=True,
            heading_regex=_re(r"^\s*(INT\.|EXT\.|EST\.|INT/EXT\.)\s+.+$"),
            min_scene_chars=5,                   # <-- was 20; keeps cue/transition scenes
            heading_consumes_line=True,
            extra_split_regexes=[
                _re(r"^\s{0,20}[A-Z][A-Z0-9 .'\-()]{2,}$"),           # character cue
                _re(r"^\s*(CUT TO:|FADE (IN|OUT):|DISSOLVE TO:)\s*$"), # transitions
            ],
        ),
        chunk=ChunkRules(window_chars=512, stride_chars=384),
    ),

    # pdf_pages: split strictly per [[PAGE_BREAK]] sentinel from the PDF parser
    "pdf_pages": Profile(
        name="pdf_pages",
        scene=SceneRules(
            break_on_blank=False,
            heading_regex=_re(r"^\s*\[\[PAGE_BREAK\]\]\s*$"),
            min_scene_chars=1,                   # strict per-page
            heading_consumes_line=True,
            extra_split_regexes=[],
        ),
        chunk=ChunkRules(window_chars=512, stride_chars=384),
    ),
}


def get_profile(name: str | None) -> Profile:
    if not name:
        return PROFILES["default"]
    return PROFILES.get(name.lower(), PROFILES["default"])

