from pipeline.core.media import export_transform_filters


def test_zoom_out_recovers_source_and_pads_canvas():
    filters = export_transform_filters(1920, 1080, (656, 0, 608, 1080), None, 30, 30)
    assert filters == [
        "scale=576:324",
        "crop=576:324:0:0",
        "pad=608:1080:(ow-iw)/2:(oh-ih)/2:black",
    ]


def test_default_scale_keeps_existing_crop():
    assert export_transform_filters(1920, 1080, (656, 0, 608, 1080), None, 100, 100) == [
        "crop=608:1080:656:0"
    ]


def test_horizontal_and_vertical_scale_are_independent():
    filters = export_transform_filters(1920, 1080, (656, 0, 608, 1080), None, 50, 80)
    assert filters[0] == "scale=960:864"
