import json

from .font_metadata import read_metadata


def test_read_metadata_color():
    base = "test/data/BungeeSpice-Regular"

    actual = read_metadata(f"{base}.ttf")
    actual = json.loads(json.dumps(actual, default=str))

    with open(f"{base}.json", encoding="utf-8") as file:
        expected = json.load(file)

    assert actual == expected

def test_read_metadata_variable():
    base = "test/data/SourceSerif4-VariableFont_opsz,wght"

    actual = read_metadata(f"{base}.ttf")
    actual = json.loads(json.dumps(actual, default=str))

    with open(f"{base}.json", encoding="utf-8") as file:
        expected = json.load(file)

    assert actual == expected
