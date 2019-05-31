"""Flint XY tile utilities"""

from collections import namedtuple
from collections import Sequence
import math


__version__ = '1.0.0'

__all__ = [
    'LngLat', 'LngLatBbox', 'Tile', 'bounds',
    'feature', 'tile', 'tiles', 'ul']


Block = namedtuple('Block', ['x', 'y'])
"""An XY flint block
Attributes
----------
x, y : int
    x and y indexes of the block.
"""


Tile = namedtuple('Tile', ['x', 'y'])
"""An XY flint tile

Attributes
----------
x, y : int
    x and y indexes of the tile.
"""


LngLat = namedtuple('LngLat', ['lng', 'lat'])
"""A longitude and latitude pair

Attributes
----------
lng, lat : float
    Longitude and latitude in decimal degrees east or north.
"""


LngLatBbox = namedtuple('LngLatBbox', ['west', 'south', 'east', 'north'])
"""A geographic bounding box

Attributes
----------
west, south, east, north : float
    Bounding values in decimal degrees.
"""

class FlinttileError(Exception):
    """Base exception"""


class InvalidLatitudeError(FlinttileError):
    """Raised when math errors occur beyond ~85 degrees N or S"""

def name(*tile):
    if len(tile) == 1:
        tile = tile[0]

    b = bounds(tile)

    name = "{0}{1:03d}_{2}{3:03d}".format('-' if b.west < 0 else '', abs(int(b.west)),
                                                   '-' if b.north < 0 else '', abs(int(b.north)))
    return name

def index(*tile):
    if len(tile) == 1:
        tile = tile[0]
    xtile, ytile = tile
    return ytile * 360 + xtile

def ul(*tile):
    """Returns the upper left longitude and latitude of a tile

    Parameters
    ----------
    tile : Tile or sequence of int
        May be be either an instance of Tile or 2 ints, X, Y.

    Returns
    -------
    LngLat

    Examples
    --------

    >>> ul(Tile(x=0, y=0))
    LngLat(lng=-180.0, lat=90)

    """
    if len(tile) == 1:
        tile = tile[0]
    xtile, ytile = tile
    lon_deg = xtile - 180.0
    lat_deg = -(ytile -90.0)
    return LngLat(lon_deg, lat_deg)


def bounds(*tile):
    """Returns the bounding box of a tile

    Parameters
    ----------
    tile : Tile or sequence of int
        May be be either an instance of Tile or 2 ints, X, Y.

    Returns
    -------
    LngLatBBox
    """
    if len(tile) == 1:
        tile = tile[0]
    xtile, ytile = tile
    a = ul(xtile, ytile)
    b = ul(xtile + 1, ytile + 1)
    return LngLatBbox(a[0], b[1], b[0], a[1])


def truncate_lnglat(lng, lat):
    if lng > 180.0:
        lng = 180.0
    elif lng < -180.0:
        lng = -180.0
    if lat > 90.0:
        lat = 90.0
    elif lat < -90.0:
        lat = -90.0
    return lng, lat


def tile(lng, lat):
    """Get the tile containing a longitude and latitude

    Parameters
    ----------
    lng, lat : float
        A longitude and latitude pair in decimal degrees.

    Returns
    -------
    Tile
    """
    xtile = int(math.floor((lng + 180.0)))
    ytile = int(math.floor(lat - 90.0)) * -1

    return Tile(xtile, ytile)



def tiles(west, south, east, north):
    """Get the tiles intersecting a geographic bounding box

    Parameters
    ----------
    west, south, east, north : sequence of float
        Bounding values in decimal degrees.

    Yields
    ------
    Tile
    """
    if west > east:
        bbox_west = (-180.0, south, east, north)
        bbox_east = (west, south, 180.0, north)
        bboxes = [bbox_west, bbox_east]
    else:
        bboxes = [(west, south, east, north)]
    for w, s, e, n in bboxes:

        # Clamp bounding values.
        w = max(-180.0, w)
        s = max(-90, s)
        e = min(180.0, e)
        n = min(90, n)


        ll = tile(w, s)
        ur = tile(e, n)

        # Clamp left x and top y at 0.
        llx = 0 if ll.x < 0 else ll.x
        ury = 0 if ur.y < 0 else ur.y

        for i in range(llx, ur.x):
            for j in range(ury, ll.y):
                yield Tile(i, j)


def feature(
        tile, fid=None, props=None, buffer=None, precision=None):
    """Get the GeoJSON feature corresponding to a tile

    Parameters
    ----------
    tile : Tile or sequence of int
        May be be either an instance of Tile or 2 ints, X, Y.
    fid : str, optional
        A feature id.
    props : dict, optional
        Optional extra feature properties.
    buffer : float, optional
        Optional buffer distance for the GeoJSON polygon.
    precision : int, optional
        GeoJSON coordinates will be truncated to this number of decimal
        places.

    Returns
    -------
    dict
    """
    west, south, east, north = bounds(tile)
    if buffer:
        west -= buffer
        south -= buffer
        east += buffer
        north += buffer
    if precision and precision >= 0:
        west, south, east, north = (
            round(v, precision) for v in (west, south, east, north))
    bbox = [
        min(west, east), min(south, north),
        max(west, east), max(south, north)]
    geom = {
        'type': 'Polygon',
        'coordinates': [[
            [west, south],
            [west, north],
            [east, north],
            [east, south],
            [west, south]]]}
    xy = index(tile)
    feat = {
        'type': 'Feature',
        'bbox': bbox,
        'id': xy,
        'geometry': geom,
        'properties': {'title': 'XY tile %s' % str(tile)}}
    if props:
        feat['properties'].update(props)
    if fid:
        feat['id'] = fid
    return feat