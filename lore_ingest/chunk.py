# lore_ingest/chunk.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional

from lore_ingest.segment import SceneSpan
from lore_ingest.segment_profiles import get_profile


@dataclass
class ChunkSpan:
    idx: int
    start: int
    end: int
    scene_idx: Optional[int] = None
    text: Optional[str] = None  # optional; persist can derive from norm_text


def make_chunks(
    text: str,
    scenes: Iterable[SceneSpan],
    *,
    window_chars: Optional[int] = None,
    stride_chars: Optional[int] = None,
    profile: str | None = None,
) -> List[ChunkSpan]:
    """
    Build sliding-window chunks within each scene.
    If `profile` is provided, its (window,stride) override defaults unless
    explicit window_chars/stride_chars are given.
    """
    prof = get_profile(profile)
    W = int(window_chars or prof.chunk.window_chars)
    S = int(stride_chars or prof.chunk.stride_chars)
    chunks: List[ChunkSpan] = []
    cidx = 0

    for s in scenes:
        start = s.start
        while start < s.end:
            end = min(start + W, s.end)
            if end <= start:
                break
            chunks.append(ChunkSpan(idx=cidx, start=start, end=end, scene_idx=s.idx))
            cidx += 1
            if end == s.end:
                break
            start = min(start + S, s.end)

    # Edge case: empty text/scene
    if not chunks and scenes:
        s0 = list(scenes)[0]
        chunks.append(ChunkSpan(idx=0, start=s0.start, end=s0.end, scene_idx=s0.idx))

    # Fix up sequential indices
    for i, c in enumerate(chunks):
        c.idx = i

    return chunks
