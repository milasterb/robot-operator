"""Where the robot is: a map, the named places on it, and the forbidden zones.

A site is a folder under sites/:

    sites/skola/
        map.yaml, map.pgm   the occupancy grid, exactly as Nav2's map_saver wrote it
        site.json           places and zones, drawn by the operator in the panel

Nothing about a room lives in the code any more. A new room is a new folder.

site.json, all coordinates in metres in the map frame:

    {
      "name": "Obývák",
      "map": "obyvak_final.yaml",
      "places": [{"name": "my room", "x": -0.35, "y": 0.0}],
      "zones":  [{"name": "the treadmill area", "kind": "forbidden",
                  "polygon": [[1.0, -1.5], [2.3, -1.5], [2.3, -0.35]]}]
    }

"kind" is always "forbidden" for now. It is spelled out so that slower zones
can be added later without changing what an existing file means.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import struct
import tempfile
import time
import unicodedata
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from gate.rules import Zone
from gate.types import Point

load_dotenv()

SITES_DIR = Path(__file__).resolve().parent.parent / "sites"

# Limits that keep a file sane. Nobody draws fifty forbidden zones in a flat;
# a file that has them was not drawn by hand.
MAX_PLACES = 50
MAX_ZONES = 50
MAX_VERTICES = 64
MIN_ZONE_AREA = 0.01        # m², about 10 x 10 cm
MAX_NAME = 40

ZONE_KINDS = {"forbidden"}

# Backdrop colours for the panel: one cool grey, opacity carries the meaning.
INK = (210, 216, 224)
ALPHA_WALL, ALPHA_FLOOR, ALPHA_UNKNOWN = 235, 28, 0


class SiteError(ValueError):
    """The site files are missing or say something that cannot be true."""


@dataclass(frozen=True)
class GridMap:
    """An occupancy grid as Nav2 saves it. Row 0 of `cells` is the top."""

    resolution: float               # metres per cell
    origin: tuple[float, float]     # world position of the bottom-left corner
    width: int
    height: int
    cells: bytes                    # one byte per cell: 0 free, 1 unknown, 2 wall

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        x0, y0 = self.origin
        return x0, y0, x0 + self.width * self.resolution, y0 + self.height * self.resolution

    def extent(self) -> tuple[float, float, float, float] | None:
        """Bounding box of the cells that were actually surveyed.

        Most of a saved grid is unknown. Fitting a view to the whole image
        would shrink the room to a smudge in the middle.
        """
        rows = [r for r in range(self.height)
                if any(c != 1 for c in self.cells[r * self.width:(r + 1) * self.width])]
        if not rows:
            return None
        cols = [c for c in range(self.width)
                if any(self.cells[r * self.width + c] != 1 for r in rows)]
        x0, y0 = self.origin
        res = self.resolution
        return (
            x0 + cols[0] * res,
            y0 + (self.height - 1 - rows[-1]) * res,
            x0 + (cols[-1] + 1) * res,
            y0 + (self.height - rows[0]) * res,
        )

    def png(self) -> bytes:
        """The grid as a transparent PNG for the panel to draw under the scene."""
        alpha = {0: ALPHA_FLOOR, 1: ALPHA_UNKNOWN, 2: ALPHA_WALL}
        raw = bytearray()
        for r in range(self.height):
            raw.append(0)  # PNG filter: none
            for c in self.cells[r * self.width:(r + 1) * self.width]:
                raw.extend((*INK, alpha[c]))
        return _png(self.width, self.height, bytes(raw))


@dataclass
class Site:
    name: str
    folder: Path
    places: dict[str, Point] = field(default_factory=dict)
    zones: list[Zone] = field(default_factory=list)
    map_file: str | None = None
    grid: GridMap | None = None
    _png: bytes | None = field(default=None, repr=False, compare=False)
    _meta: dict | None = field(default=None, repr=False, compare=False)

    def to_json(self) -> dict:
        """What site.json holds. The map itself stays in its own files."""
        return {
            "name": self.name,
            "map": self.map_file,
            "places": [
                {"name": n, "x": round(p.x, 3), "y": round(p.y, 3)}
                for n, p in self.places.items()
            ],
            "zones": [
                {"name": z.name, "kind": "forbidden",
                 "polygon": [[round(p.x, 3), round(p.y, 3)] for p in z.polygon]}
                for z in self.zones
            ],
        }

    def map_png(self) -> bytes:
        """The backdrop image, rendered once per loaded site."""
        if self.grid is None:
            raise SiteError("This site has no map.")
        if self._png is None:
            self._png = self.grid.png()
        return self._png

    def describe_map(self) -> dict | None:
        """Map metadata for the panel; the image is served separately."""
        if self.grid is None:
            return None
        if self._meta is None:
            self._meta = self._describe(self.grid)
        return self._meta

    @staticmethod
    def _describe(g: GridMap) -> dict:
        extent = g.extent()
        return {
            "resolution": g.resolution,
            "origin": list(g.origin),
            "size": [g.width, g.height],
            "extent": None if extent is None else
                      {"x0": extent[0], "y0": extent[1], "x1": extent[2], "y1": extent[3]},
        }


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #

def site_folder(name: str | None = None) -> Path:
    """The folder of the site called `name`, or of the one SITE names."""
    name = name or os.environ.get("SITE", "demo")
    if not name.replace("_", "").replace("-", "").isalnum():
        raise SiteError(f"SITE must be a plain folder name, not {name!r}")
    return SITES_DIR / name


def load_site(folder: Path) -> Site:
    path = folder / "site.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise SiteError(f"No site at {folder} (missing site.json)") from e
    except json.JSONDecodeError as e:
        raise SiteError(f"{path} is not valid JSON: {e}") from e

    map_file = data.get("map")
    grid = load_grid(folder / map_file) if map_file else None
    return parse_site(data, folder, grid)


def parse_site(data: dict, folder: Path, grid: GridMap | None) -> Site:
    """Turn site.json content into a Site, refusing anything inconsistent.

    This is the one door every change comes through, whether the operator
    drew it in the panel or someone edited the file by hand.
    """
    if not isinstance(data, dict):
        raise SiteError("A site must be a JSON object.")
    name = _name(data.get("name") or folder.name, "site")

    raw_places = data.get("places") or []
    raw_zones = data.get("zones") or []
    if not isinstance(raw_places, list) or not isinstance(raw_zones, list):
        raise SiteError("places and zones must be lists.")
    if len(raw_places) > MAX_PLACES:
        raise SiteError(f"At most {MAX_PLACES} places.")
    if len(raw_zones) > MAX_ZONES:
        raise SiteError(f"At most {MAX_ZONES} zones.")

    places: dict[str, Point] = {}
    for item in raw_places:
        if not isinstance(item, dict):
            raise SiteError("Each place must be an object with name, x and y.")
        pname = _name(item.get("name"), "place")
        if pname.lower() in (n.lower() for n in places):
            raise SiteError(f"Two places are called {pname!r}.")
        point = _point(item.get("x"), item.get("y"), f"place {pname!r}")
        _inside_map(point, grid, f"Place {pname!r}")
        places[pname] = point

    zones: list[Zone] = []
    for item in raw_zones:
        if not isinstance(item, dict):
            raise SiteError("Each zone must be an object with name and polygon.")
        zname = _name(item.get("name"), "zone")
        if zname.lower() in (z.name.lower() for z in zones):
            raise SiteError(f"Two zones are called {zname!r}.")
        kind = item.get("kind", "forbidden")
        if kind not in ZONE_KINDS:
            raise SiteError(f"Zone {zname!r} has unknown kind {kind!r}.")
        polygon = item.get("polygon")
        if not isinstance(polygon, list) or not 3 <= len(polygon) <= MAX_VERTICES:
            raise SiteError(
                f"Zone {zname!r} needs between 3 and {MAX_VERTICES} corners.")
        points = []
        for corner in polygon:
            if not isinstance(corner, (list, tuple)) or len(corner) != 2:
                raise SiteError(f"Zone {zname!r} has a corner that is not [x, y].")
            p = _point(corner[0], corner[1], f"zone {zname!r}")
            _inside_map(p, grid, f"Zone {zname!r}")
            points.append(p)
        if _self_intersects(points):
            raise SiteError(
                f"Zone {zname!r} crosses itself, so inside and outside are ambiguous.")
        if abs(_area(points)) < MIN_ZONE_AREA:
            raise SiteError(f"Zone {zname!r} is too small to mean anything.")
        zones.append(Zone(zname, points))

    map_file = data.get("map")
    if map_file is not None and not isinstance(map_file, str):
        raise SiteError("map must be the name of a .yaml file.")
    return Site(name, folder, places, zones, map_file, grid)


def save_site(site: Site) -> None:
    """Write site.json so that a crash halfway leaves the old file intact."""
    path = site.folder / "site.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(_format(site.to_json()), encoding="utf-8")
    os.replace(tmp, path)


def list_sites() -> list[dict]:
    """Every folder under sites/ that holds a site.json, by name."""
    found = []
    for folder in sorted(SITES_DIR.iterdir()):
        if folder.name.startswith("."):
            continue  # a map still being checked by create_site
        path = folder / "site.json"
        if not path.is_file():
            continue
        try:
            name = json.loads(path.read_text(encoding="utf-8")).get("name") or folder.name
        except (json.JSONDecodeError, AttributeError):
            name = folder.name
        found.append({"folder": folder.name, "name": name})
    return found


def folder_name(display_name: str) -> str:
    """'Učebna 204' -> 'ucebna-204': a folder name safe on any filesystem."""
    ascii_ = unicodedata.normalize("NFKD", display_name).encode("ascii", "ignore").decode()
    slug = "".join(c if c.isalnum() else "-" for c in ascii_.lower())
    slug = "-".join(part for part in slug.split("-") if part)
    if not slug:
        raise SiteError("The name needs at least one letter or digit.")
    return slug[:MAX_NAME]


def create_site(display_name: str, yaml_text: str, pgm: bytes) -> Path:
    """Make a new site folder from a map that map_saver wrote.

    The map is checked before anything lands in sites/: it is written to a
    scratch folder, read back exactly as a running server would read it, and
    only then moved into place. A half-written or unreadable map never
    becomes a site.
    """
    name = _name(display_name, "site")
    target = SITES_DIR / folder_name(name)
    if target.exists():
        raise SiteError(f"A site called {target.name!r} already exists.")

    meta = _read_map_yaml(yaml_text)
    scratch = Path(tempfile.mkdtemp(prefix=".new-", dir=SITES_DIR))
    try:
        (scratch / "map.pgm").write_bytes(pgm)
        (scratch / "map.yaml").write_text(_map_yaml(meta), encoding="utf-8")
        grid = load_grid(scratch / "map.yaml")
        if grid.extent() is None:
            raise SiteError("That map is entirely unknown: nothing was surveyed.")
        site = Site(name, scratch, map_file="map.yaml", grid=grid)
        save_site(site)
        os.replace(scratch, target)
    except Exception:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    return target


def log_change(folder: Path, before: dict, after: dict) -> None:
    """Append one line to the site's change log: when, and what differs.

    The fences decide where the robot may go, so who moved them and when
    is worth keeping. The panel has no users, so 'who' is the operator.
    """
    def names(d: dict, key: str) -> set[str]:
        return {i["name"] for i in d.get(key) or []}

    entry = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "places_added": sorted(names(after, "places") - names(before, "places")),
        "places_removed": sorted(names(before, "places") - names(after, "places")),
        "zones_added": sorted(names(after, "zones") - names(before, "zones")),
        "zones_removed": sorted(names(before, "zones") - names(after, "zones")),
        "zones_changed": sorted(
            z["name"] for z in after.get("zones") or []
            for old in before.get("zones") or []
            if old["name"] == z["name"] and old["polygon"] != z["polygon"]
        ),
        "after": after,
    }
    with open(folder / "changes.log", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _format(data: dict) -> str:
    """JSON laid out for people: one place per line, a zone's corners together.

    The file is meant to be read and, at a pinch, edited by hand, so the
    layout follows the meaning rather than json.dumps' indentation rules.
    """
    dump = lambda v: json.dumps(v, ensure_ascii=False)

    def block(key: str, items: list[str], last: bool) -> list[str]:
        end = "" if last else ","
        if not items:
            return [f'  "{key}": []{end}']
        return [f'  "{key}": [', ",\n".join(items), f"  ]{end}"]

    places = [f"    {dump(p)}" for p in data["places"]]
    zones = [
        f'    {{"name": {dump(z["name"])}, "kind": {dump(z["kind"])},\n'
        f'     "polygon": [{", ".join(dump(c) for c in z["polygon"])}]}}'
        for z in data["zones"]
    ]
    lines = [
        "{",
        f'  "name": {dump(data["name"])},',
        f'  "map": {dump(data["map"])},',
        *block("places", places, last=False),
        *block("zones", zones, last=True),
        "}",
    ]
    return "\n".join(lines) + "\n"


# How much of the grid may differ and still count as the same map. A map
# copied from the robot matches exactly; this only absorbs a few cells
# touched up in an image editor.
MAP_MATCH = 0.98


def compare_with_robot(grid: GridMap | None, robot_map: dict) -> str | None:
    """None if the robot is localising against this map, else what differs.

    `robot_map` is Nav2's own OccupancyGrid as the adapter fetched it:
    resolution, width, height, origin (x, y) and data, row 0 at the origin
    (the bottom of the image), -1 unknown, 0 free, 100 occupied.
    """
    if grid is None:
        return "This site has no map to compare against."
    if abs(robot_map["resolution"] - grid.resolution) > 1e-6:
        return (f"The robot's map has {robot_map['resolution']:.3f} m cells, "
                f"this one {grid.resolution:.3f} m.")
    if (robot_map["width"], robot_map["height"]) != (grid.width, grid.height):
        return (f"The robot's map is {robot_map['width']}x{robot_map['height']} "
                f"cells, this one {grid.width}x{grid.height}.")
    ox, oy = robot_map["origin"]
    if abs(ox - grid.origin[0]) > 1e-3 or abs(oy - grid.origin[1]) > 1e-3:
        return "The two maps start at different origins."

    data = robot_map["data"]
    if len(data) != grid.width * grid.height:
        return "The robot sent a map of the wrong length."
    same = 0
    for row in range(grid.height):
        mine = grid.cells[(grid.height - 1 - row) * grid.width:(grid.height - row) * grid.width]
        theirs = data[row * grid.width:(row + 1) * grid.width]
        for c, d in zip(mine, theirs):
            same += c == (1 if d < 0 else 2 if d >= 65 else 0 if d <= 25 else 1)
    share = same / len(data)
    if share < MAP_MATCH:
        return f"Only {share:.0%} of the two maps agree."
    return None


def load_grid(yaml_path: Path) -> GridMap:
    """Read a map saved by Nav2's map_saver: a small YAML file and a PGM."""
    try:
        meta = _read_map_yaml(yaml_path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise SiteError(f"Map file {yaml_path} not found") from e

    # map_saver writes the image path as it was on the robot, often absolute
    # (/root/maps/skola.pgm). The .pgm travels next to the .yaml.
    image = yaml_path.parent / Path(meta["image"]).name
    width, height, maxval, pixels = _read_pgm(image)

    res = float(meta["resolution"])
    origin = meta["origin"]
    negate = int(meta.get("negate", 0))
    occupied = float(meta.get("occupied_thresh", 0.65))
    free = float(meta.get("free_thresh", 0.25))
    if res <= 0:
        raise SiteError(f"{yaml_path}: resolution must be positive")

    # map_saver writes unknown cells as exactly 205 in trinary mode. Read
    # through the thresholds alone, 205 is a probability of 0.196 — under
    # the 0.25 free threshold the same tool writes — and the whole unsurveyed
    # grid would come back as open floor. So the marker value is honoured
    # first and the thresholds decide the rest.
    unknown_marker = 205 if meta.get("mode", "trinary") == "trinary" else None
    cells = bytearray(width * height)
    for i, v in enumerate(pixels):
        if v == unknown_marker:
            cells[i] = 1
            continue
        shade = v / maxval
        p = shade if negate else 1.0 - shade   # occupancy probability
        cells[i] = 2 if p > occupied else 0 if p < free else 1
    return GridMap(res, (float(origin[0]), float(origin[1])), width, height, bytes(cells))


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _name(value, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SiteError(f"Every {what} needs a name.")
    value = " ".join(value.split())
    if len(value) > MAX_NAME:
        raise SiteError(f"The {what} name {value!r} is longer than {MAX_NAME} characters.")
    return value


def _point(x, y, where: str) -> Point:
    if isinstance(x, bool) or isinstance(y, bool):
        raise SiteError(f"Coordinates of {where} must be numbers.")
    try:
        fx, fy = float(x), float(y)
    except (TypeError, ValueError) as e:
        raise SiteError(f"Coordinates of {where} must be numbers.") from e
    if not (math.isfinite(fx) and math.isfinite(fy)):
        raise SiteError(f"Coordinates of {where} must be finite.")
    return Point(fx, fy)


def _inside_map(p: Point, grid: GridMap | None, what: str) -> None:
    if grid is None:
        return
    x0, y0, x1, y1 = grid.bounds
    if not (x0 <= p.x <= x1 and y0 <= p.y <= y1):
        raise SiteError(f"{what} lies outside the map.")


def _area(points: list[Point]) -> float:
    return 0.5 * sum(
        a.x * b.y - b.x * a.y
        for a, b in zip(points, points[1:] + points[:1])
    )


def _self_intersects(points: list[Point]) -> bool:
    """Do any two edges that are not neighbours touch or cross?"""
    n = len(points)
    edges = [(points[i], points[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue  # neighbours share a corner by definition
            if _segments_touch(*edges[i], *edges[j]):
                return True
    return False


def _segments_touch(a: Point, b: Point, c: Point, d: Point) -> bool:
    def orient(p, q, r):
        v = (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x)
        return 0 if abs(v) < 1e-12 else (1 if v > 0 else -1)

    def on_segment(p, q, r):
        return (min(p.x, r.x) - 1e-12 <= q.x <= max(p.x, r.x) + 1e-12
                and min(p.y, r.y) - 1e-12 <= q.y <= max(p.y, r.y) + 1e-12)

    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    if o1 != o2 and o3 != o4:
        return True
    return ((o1 == 0 and on_segment(a, c, b)) or (o2 == 0 and on_segment(a, d, b))
            or (o3 == 0 and on_segment(c, a, d)) or (o4 == 0 and on_segment(c, b, d)))


def _map_yaml(meta: dict) -> str:
    """A map.yaml pointing at map.pgm, whatever the image was called before."""
    origin = ", ".join(str(v) for v in meta["origin"])
    lines = [
        "image: map.pgm",
        f"mode: {meta.get('mode', 'trinary')}",
        f"resolution: {meta['resolution']}",
        f"origin: [{origin}]",
        f"negate: {meta.get('negate', 0)}",
        f"occupied_thresh: {meta.get('occupied_thresh', 0.65)}",
        f"free_thresh: {meta.get('free_thresh', 0.25)}",
    ]
    return "\n".join(lines) + "\n"


def _read_map_yaml(text: str) -> dict:
    """The handful of flat keys map_saver writes. Not a general YAML parser."""
    meta: dict = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = (s.strip() for s in line.split(":", 1))
        if value.startswith("["):
            meta[key] = [float(v) for v in value.strip("[]").split(",") if v.strip()]
        else:
            meta[key] = value
    for key in ("image", "resolution", "origin"):
        if key not in meta:
            raise SiteError(f"Map YAML has no {key!r}")
    return meta


def _read_pgm(path: Path) -> tuple[int, int, int, bytes]:
    """Binary (P5) PGM, the format map_saver writes."""
    try:
        data = path.read_bytes()
    except FileNotFoundError as e:
        raise SiteError(f"Map image {path} not found") from e

    tokens: list[bytes] = []
    i = 0
    while len(tokens) < 4:
        while i < len(data) and data[i:i + 1].isspace():
            i += 1
        if data[i:i + 1] == b"#":
            while i < len(data) and data[i:i + 1] not in (b"\n", b"\r"):
                i += 1
            continue
        start = i
        while i < len(data) and not data[i:i + 1].isspace():
            i += 1
        tokens.append(data[start:i])
    i += 1  # the single whitespace byte before the pixels

    if tokens[0] != b"P5":
        raise SiteError(f"{path} is not a binary PGM (P5)")
    width, height, maxval = (int(t) for t in tokens[1:])
    if maxval > 255:
        raise SiteError(f"{path} uses 16-bit pixels, which map_saver never writes")
    pixels = data[i:i + width * height]
    if len(pixels) != width * height:
        raise SiteError(f"{path} is shorter than its header says")
    return width, height, maxval, pixels


def _png(width: int, height: int, rgba_rows: bytes) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(rgba_rows, 9)) + chunk(b"IEND", b""))
