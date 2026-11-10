from __future__ import annotations

import json
from pathlib import Path

import pytest

import layout.site as site_mod
from layout import SiteError, create_site, load_site, parse_site, save_site
from layout.site import _format, load_grid

OBYVAK = Path(__file__).resolve().parent.parent / "sites" / "obyvak"
SQUARE = [[0, 0], [1, 0], [1, 1], [0, 1]]


def site(**overrides) -> dict:
    data = {
        "name": "Test",
        "map": None,
        "places": [{"name": "the door", "x": 2.0, "y": 0.5}],
        "zones": [{"name": "the box", "kind": "forbidden", "polygon": SQUARE}],
    }
    data.update(overrides)
    return data


def test_a_valid_site_parses():
    s = parse_site(site(), Path("x"), None)
    assert list(s.places) == ["the door"]
    assert s.zones[0].name == "the box"
    assert len(s.zones[0].polygon) == 4


@pytest.mark.parametrize("bad, message", [
    (site(places=[{"name": "a", "x": 0, "y": 0}, {"name": "A", "x": 1, "y": 1}]), "Two places"),
    (site(zones=[{"name": "z", "polygon": SQUARE}, {"name": "Z", "polygon": SQUARE}]), "Two zones"),
    (site(zones=[{"name": "bowtie", "polygon": [[0, 0], [1, 1], [1, 0], [0, 1]]}]), "crosses itself"),
    (site(zones=[{"name": "sliver", "polygon": [[0, 0], [0.05, 0], [0.05, 0.05]]}]), "too small"),
    (site(zones=[{"name": "line", "polygon": [[0, 0], [1, 0]]}]), "between 3"),
    (site(zones=[{"name": "slow", "kind": "slow", "polygon": SQUARE}]), "unknown kind"),
    (site(places=[{"name": "p", "x": "far", "y": 0}]), "must be numbers"),
    (site(places=[{"name": "p", "x": float("nan"), "y": 0}]), "finite"),
    (site(places=[{"name": "", "x": 0, "y": 0}]), "needs a name"),
    (site(places=[{"name": "p", "x": True, "y": 0}]), "must be numbers"),
])
def test_impossible_sites_are_refused(bad, message):
    with pytest.raises(SiteError, match=message):
        parse_site(bad, Path("x"), None)


def test_points_must_lie_on_the_map():
    grid = load_grid(OBYVAK / "map.yaml")
    with pytest.raises(SiteError, match="outside the map"):
        parse_site(site(places=[{"name": "moon", "x": 50, "y": 0}]), OBYVAK, grid)


def test_unknown_cells_are_not_read_as_floor():
    """205 is map_saver's 'unknown'. Through the thresholds alone it would be
    free floor, and the room would appear to fill the whole 19 m grid."""
    grid = load_grid(OBYVAK / "map.yaml")
    x0, y0, x1, y1 = grid.extent()
    assert (x0, y0) == pytest.approx((-2.75, -1.75))
    assert (x1, y1) == pytest.approx((5.85, 1.90))


def test_the_map_renders_as_png():
    png = load_grid(OBYVAK / "map.yaml").png()
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_the_saved_file_reads_back_the_same(tmp_path):
    s = parse_site(site(), tmp_path, None)
    save_site(s)
    again = load_site(tmp_path)
    assert again.to_json() == s.to_json()
    assert json.loads(_format(s.to_json())) == s.to_json()


def test_a_new_site_is_made_from_a_saved_map(tmp_path, monkeypatch):
    monkeypatch.setattr(site_mod, "SITES_DIR", tmp_path)
    yaml_text = (OBYVAK / "map.yaml").read_text().replace("map.pgm", "room.pgm")
    folder = create_site("Učebna 204", yaml_text, (OBYVAK / "map.pgm").read_bytes())

    assert folder.name == "ucebna-204"
    s = load_site(folder)
    assert s.name == "Učebna 204"
    assert s.grid is not None and s.places == {} and s.zones == []
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".")]

    with pytest.raises(SiteError, match="already exists"):
        create_site("Učebna 204", yaml_text, (OBYVAK / "map.pgm").read_bytes())


def test_a_broken_map_never_becomes_a_site(tmp_path, monkeypatch):
    monkeypatch.setattr(site_mod, "SITES_DIR", tmp_path)
    yaml_text = (OBYVAK / "map.yaml").read_text()
    with pytest.raises(SiteError):
        create_site("Broken", yaml_text, b"P5\n384 384\n255\n" + b"\x00" * 10)
    assert list(tmp_path.iterdir()) == []
