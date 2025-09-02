# lore_ingest/segment.py
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from lore_ingest.segment_profiles import get_profile, Profile


@dataclass
class SceneSpan:
    idx: int
    start: int
    end: int
    heading: Optional[str] = None


def _is_blank(line: str) -> bool:
    return len(line.strip()) == 0


def segment_to_scenes(text: str, profile: str | None = None) -> List[SceneSpan]:
    """
    Partition `text` into scenes according to the selected profile.
    """
    prof: Profile = get_profile(profile)
    lines = text.splitlines(keepends=True)
    scenes: List[SceneSpan] = []

    pos = 0
    cur_start = 0
    cur_heading: Optional[str] = None

    def _emit(end_pos: int):
        nonlocal cur_start, cur_heading
        if end_pos <= cur_start:
            return
        span_len = end_pos - cur_start
        if span_len < prof.scene.min_scene_chars and scenes:
            return
        scenes.append(SceneSpan(idx=len(scenes), start=cur_start, end=end_pos, heading=cur_heading))
        cur_heading = None

    heading_re = prof.scene.heading_regex

    for line in lines:
        line_start = pos
        pos += len(line)

        # Heading boundary?
        if heading_re and heading_re.match(line):
            if line_start > cur_start:
                _emit(line_start)
            # Start next scene either at the heading line start or *after* it
            cur_start = pos if prof.scene.heading_consumes_line else line_start
            cur_heading = None  # we don't carry the sentinel/heading as scene heading
            continue

        # Blank-line boundary?
        if prof.scene.break_on_blank and _is_blank(line):
            _emit(line_start)
            cur_start = pos
            cur_heading = None

    # flush tail
    if pos > cur_start:
        _emit(pos)

    if not scenes:
        scenes.append(SceneSpan(idx=0, start=0, end=len(text), heading=None))

    # reindex
    for i, s in enumerate(scenes):
        s.idx = i

    return scenes
