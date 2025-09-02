from lore_ingest.segment import segment_to_scenes


def test_segment_basic_with_heading():
    text = (
        "CHAPTER I\n"
        "The beginning.\n"
        "\n"
        "\n"
        "Scene Two\n"
        "More text.\n"
    )
    scenes = segment_to_scenes(text)
    assert len(scenes) == 2

    s0 = scenes[0]
    s1 = scenes[1]
    assert s0.idx == 0 and s1.idx == 1
    assert s0.start == 0 and s1.start > s0.end
    assert s1.end == len(text)
    assert s0.heading and s0.heading.startswith("CHAPTER")
