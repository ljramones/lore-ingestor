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
    Profile-aware segmentation.
    - Headings split scenes (optionally consuming the heading line)
    - Optional extra split regexes (e.g., screenplay character cues, transitions)
    - Optional blank-line splitting
    - Optional ignore fenced code blocks (markdown)
    """
    prof: Profile = get_profile(profile)
    rules = prof.scene
    lines = text.splitlines(keepends=True)
    scenes: List[SceneSpan] = []

    pos = 0
    cur_start = 0
    cur_heading: Optional[str] = None
    in_fence = False

    def _emit(end_pos: int):
        nonlocal cur_start, cur_heading
        if end_pos <= cur_start:
            return
        span_len = end_pos - cur_start
        if span_len < rules.min_scene_chars and scenes:
            return
        scenes.append(SceneSpan(idx=len(scenes), start=cur_start, end=end_pos, heading=cur_heading))
        cur_heading = None

    for line in lines:
        line_start = pos
        pos += len(line)

        # Fenced code tracking (for markdown-like profiles)
        if rules.ignore_fenced_code and (rules.fence_open_regex or rules.fence_close_regex):
            if not in_fence and rules.fence_open_regex and rules.fence_open_regex.match(line):
                in_fence = True
            elif in_fence and rules.fence_close_regex and rules.fence_close_regex.match(line):
                in_fence = False

        # Heading boundary?
        if rules.heading_regex and not in_fence and rules.heading_regex.match(line):
            if line_start > cur_start:
                _emit(line_start)
            # Start next scene either at the heading line start or *after* it
            cur_start = pos if rules.heading_consumes_line else line_start
            cur_heading = None
            continue

        # Extra splitters (e.g., screenplay character cues / transitions)
        if not in_fence and rules.extra_split_regexes:
            for rx in rules.extra_split_regexes:
                if rx.match(line):
                    if line_start > cur_start:
                        _emit(line_start)
                    cur_start = line_start  # start new scene at the cue/transition
                    cur_heading = None
                    break

        # Blank-line boundary?
        if rules.break_on_blank and not in_fence and _is_blank(line):
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
