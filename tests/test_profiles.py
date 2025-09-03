# tests/test_profiles.py
from lore_ingest.segment import segment_to_scenes


def test_markdown_fenced_code_no_split():
    text = (
        "# Intro\n"
        "Some prose.\n\n"
        "```python\n"
        "# inside fence with a heading\n"
        "# NotAHeading\n"
        "```\n\n"
        "## Next Section\n"
        "More prose.\n"
    )
    scenes = segment_to_scenes(text, profile="markdown")
    # Expect two scenes: before fence and after "## Next Section"
    assert len(scenes) == 2
    # Ensure fence didn't create a split on its own
    s0, s1 = scenes
    assert s0.end < s1.end and s0.start == 0


def test_screenplay_character_cue_split():
    text = (
        "INT. HOUSE - NIGHT\n"
        "The room is dark.\n\n"
        "JOHN DOE\n"
        "I can't see a thing.\n\n"
        "CUT TO:\n"
        "EXT. STREET - DAY\n"
        "Cars rush by.\n"
    )
    scenes = segment_to_scenes(text, profile="screenplay")
    # Expected splits: after slugline, at character cue, at transition, at next slugline
    assert len(scenes) >= 3


def test_pdf_pages_strict():
    text = "Page One\n[[PAGE_BREAK]]\nPage Two\n[[PAGE_BREAK]]\nPage Three\n"
    scenes = segment_to_scenes(text, profile="pdf_pages")
    assert len(scenes) == 3
    # per-page strict: min_scene_chars is low (1), so all pages produce scenes
