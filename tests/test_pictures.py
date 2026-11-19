"""The two pictures beside each sighting: the satellite image turned to the way you look, and the window view
(terrain from the DEM, colour from the satellite mosaic). No network: imagery and terrain are stood in for."""
import math

import numpy as np
from PIL import Image

from planeview import aerial, views


def _north_red(lat, lon, km, S):
    """A north-up crop whose northern half is red and southern half blue; positions are passed straight through."""
    im = Image.new("RGB", (S, S), (0, 0, 255))
    im.paste((255, 0, 0), (0, 0, S, S // 2))
    return im, lambda la, lo: (50 + (lo - lon) * 1000, 50 - (la - lat) * 1000)


def test_the_satellite_image_is_turned_to_the_way_you_look(monkeypatch):
    monkeypatch.setattr(aerial, "_crop_north", _north_red)
    S = aerial.S
    red = lambda im, x, y: im.getpixel((int(x * S), int(y * S)))[0] > 200
    north_up, _ = aerial.crop(50.0, 0.0, 10)
    assert red(north_up, 0.5, 0.1) and not red(north_up, 0.5, 0.9)
    east, to_px = aerial.crop(50.0, 0.0, 10, up=90)              # looking east: north is on your left
    assert red(east, 0.1, 0.5) and not red(east, 0.9, 0.5)
    x, y = to_px(50.02, 0.0)                                      # a place to the north is drawn on the left too
    assert x < 30 and abs(y - 50) < 1
    x, y = to_px(50.0, 0.02)                                      # and what lies beyond (east) is at the top
    assert y < 30 and abs(x - 50) < 1
    south, _ = aerial.crop(50.0, 0.0, 10, up=180)
    assert red(south, 0.5, 0.9) and not red(south, 0.5, 0.1)


def test_the_overlay_says_which_way_is_up(monkeypatch):
    monkeypatch.setattr(aerial, "_crop_north", _north_red)
    ev = dict(kind="settlement", name="Town", lat=47.28, lon=11.51, pop=13000, brg=200.0, ac=[47.30, 11.52, 5000, 247.0])
    im, ov = aerial.render(ev, None)
    assert im.size == (aerial.S, aerial.S)
    assert ov["look"] == dict(up=200, x=50.0, y=94.0, compass="south-south-west")
    assert ov["labels"][0] == dict(n="Town", x=50.0, y=50.0, main=True)


def test_ground_colour_comes_from_the_imagery_at_a_zoom_set_by_distance(monkeypatch):
    asked = []

    def tile(z, x, y):
        asked.append(z)
        return None if z >= 13 else np.full((256, 256, 3), 10.0 * z, np.float32)
    monkeypatch.setattr(views, "_tex_tile", tile)
    la, lo = np.full(3, 47.3), np.full(3, 11.4)
    col = views.texture(la, lo, np.array([0.5, 20.0, 200.0]))
    assert np.isnan(col[0]).all()                          # nearest ground wants zoom 13+, which is missing here
    assert col[1, 0] > col[2, 0] > 0                       # farther ground is read from a coarser zoom
    assert min(asked) >= views.TEX_ZOOM[0] and max(asked) <= views.TEX_ZOOM[1]


def test_rays_stop_at_the_terrain_not_at_sea_level(monkeypatch):
    monkeypatch.setattr(views, "dem_sample", lambda la, lo, allow_fetch=True: np.full(np.shape(la), 1000.0, np.float32))
    AZ, EL = np.array([[180.0, 180.0]]), np.array([[-10.0, 5.0]])           # one ray 10 degrees down, one at the sky
    d, z = views.ground_terrain(47.0, 11.0, 3000.0, AZ, EL)
    assert abs(d[0, 0] - 2000 / math.tan(math.radians(10)) / 1000) < 0.15   # a plateau 2,000 m below: 11.3 km out
    assert z[0, 0] == 1000 and np.isnan(d[0, 1])
