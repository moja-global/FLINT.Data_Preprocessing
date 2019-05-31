"""scripts/optimize_rasterstack.py

Convert some raster files to optimized formats for use with moja flint.
"""

from typing import (Sequence, Iterator, Union, Dict, Tuple, TypeVar)
import os
import math
import warnings
import contextlib
import tempfile
import logging
import json

from pathlib import Path
import numpy as np

import click
import tqdm

import rasterio
from rasterio.io import DatasetReader, MemoryFile
from rasterio.vrt import WarpedVRT
from rasterio.enums import Resampling
from rasterio.env import GDALVersion

from affine import Affine

import flintdata.flinttile

from flintdata.scripts.click_types import RasterPattern, RasterPatternType, PathlibPath

logger = logging.getLogger(__name__)

IN_MEMORY_THRESHOLD = 16000 * 16000

CACHEMAX = 1024 * 1024 * 512  # 512 MB

RESAMPLING_METHODS = {
    'average': Resampling.average,
    'nearest': Resampling.nearest,
    'bilinear': Resampling.bilinear,
    'cubic': Resampling.cubic
}

GDAL_CONFIG = {
    'GDAL_TIFF_INTERNAL_MASK': True,
    'GDAL_TIFF_OVR_BLOCKSIZE': 400,
    'GDAL_CACHEMAX': CACHEMAX,
    'GDAL_SWATH_SIZE': 2 * CACHEMAX,
    'GDAL_DISABLE_READDIR_ON_OPEN': 'EMPTY_DIR'
}

FLINT_TILE_PROFILE = {
    'count': 1,
    'driver': 'GTiff',
    'interleave': 'pixel',
    'tiled': True,
    'blockxsize': 400,
    'blockysize': 400,
    'width': 4000,
    'height': 4000,
    'BIGTIFF': 'IF_SAFER'
}

Number = TypeVar('Number', int, float)

_TARGET_CRS: str = 'epsg:4326'

def _translate_type(dtype):
    return {
        rasterio.uint8: 'UInt8',
        rasterio.uint16: 'UInt16',
        rasterio.uint32: 'UInt32',
        rasterio.int16: 'Int16',
        rasterio.int32: 'Int32',
        rasterio.float32: 'Float32',
        rasterio.float64: 'Float64'
    }[dtype]


def _prefered_compression_method() -> str:
    if not GDALVersion.runtime().at_least('2.3'):
        return 'DEFLATE'

    # check if we can use ZSTD (fails silently for GDAL < 2.3)
    dummy_profile = dict(driver='GTiff', height=1, width=1, count=1, dtype='uint8')
    try:
        with MemoryFile() as memfile, memfile.open(compress='ZSTD', **dummy_profile):
            pass
    except Exception as exc:
        if 'missing codec' not in str(exc):
            raise
    else:
        return 'ZSTD'

    return 'DEFLATE'

def _calculate_default_transform(src_crs: Union[Dict[str, str], str],
                                 _TARGET_CRS: Union[Dict[str, str], str],
                                 width: int,
                                 height: int,
                                 *bounds: Number) -> Tuple[Affine, int, int]:
    """A more stable version of GDAL's default transform.

    Ensures that the number of pixels along the image's shortest diagonal remains
    the same in both CRS, without enforcing square pixels.

    Bounds are in order (west, south, east, north).
    """
    from rasterio import warp, transform

    if len(bounds) != 4:
        raise ValueError('Bounds must contain 4 values')

    if src_crs is None:
        src_crs = _TARGET_CRS

    # transform image corners to target CRS
    dst_corner_sw, dst_corner_nw, dst_corner_se, dst_corner_ne = (
        list(zip(*warp.transform(
            src_crs, _TARGET_CRS,
            [bounds[0], bounds[0], bounds[2], bounds[2]],
            [bounds[1], bounds[3], bounds[1], bounds[3]]
        )))
    )

    # determine inner bounding box of corners in target CRS
    dst_corner_bounds = [
        max(dst_corner_sw[0], dst_corner_nw[0]),
        max(dst_corner_sw[1], dst_corner_se[1]),
        min(dst_corner_se[0], dst_corner_ne[0]),
        min(dst_corner_nw[1], dst_corner_ne[1])
    ]

    # compute target resolution
    dst_corner_transform = transform.from_bounds(*dst_corner_bounds, width=width, height=height)
    target_res = (dst_corner_transform.a, dst_corner_transform.e)

    # get transform spanning whole bounds (not just projected corners)
    dst_bounds = warp.transform_bounds(src_crs, _TARGET_CRS, *bounds)
    dst_width = math.ceil((dst_bounds[2] - dst_bounds[0]) / target_res[0])
    dst_height = math.ceil((dst_bounds[1] - dst_bounds[3]) / target_res[1])
    dst_transform = transform.from_bounds(*dst_bounds, width=dst_width, height=dst_height)

    return dst_transform, dst_width, dst_height

def _info(src):
    info = dict(src.profile)
    info['shape'] = (info['height'], info['width'])
    info['bounds'] = src.bounds

    if src.crs:
        epsg = src.crs.to_epsg()
        if epsg:
            info['crs'] = 'EPSG:{}'.format(epsg)
        else:
            info['crs'] = src.crs.to_string()
    else:
        info['crs'] = None

    info['res'] = src.res
    info['colorinterp'] = [ci.name for ci in src.colorinterp]
    info['units'] = [units or None for units in src.units]
    info['descriptions'] = src.descriptions
    info['indexes'] = src.indexes
    info['mask_flags'] = [[
        flag.name for flag in flags] for flags in src.mask_flag_enums]

    if src.crs:
        info['lnglat'] = src.lnglat()

    gcps, gcps_crs = src.gcps

    if gcps:
        info['gcps'] = {'points': [p.asdict() for p in gcps]}
        if gcps_crs:
            epsg = gcps_crs.to_epsg()
            if epsg:
                info['gcps']['crs'] = 'EPSG:{}'.format(epsg)
            else:
                info['gcps']['crs'] = src.crs.to_string()
        else:
            info['gcps']['crs'] = None
    return info


def _writeLayerInfo(src, layerName, outFld, nLayers=None):
    info = _info(src)
    layerInfo = {
        'layer_type': 'GridLayer',
        'layer_prefix': layerName,
        "layer_data": _translate_type(info['dtype']),
        'tileLatSize': 1.0,
        'tileLonSize': 1.0,
        'blockLatSize': 0.1,
        'blockLonSize': 0.1,
        'cellLatSize': abs(info['transform'].d),
        'cellLonSize': info['transform'].a,
        'coordinateSystem': info['crs'],
        'cornerCoordinates': info['bounds'],
        'size': info["shape"]
    }
    if 'nodata' in info:
        layerInfo['nodata'] = info['nodata']
    if (nLayers):
        layerInfo['nLayers'] = nLayers
        layerInfo['layer_type'] = 'StackLayer'

    with open(os.path.join(outFld, layerName + '.json'), 'w') as f:
        json.dump(layerInfo, f, ensure_ascii=False, sort_keys=True, indent=2)

@contextlib.contextmanager
def _named_tempfile(basedir: Union[str, Path]) -> Iterator[str]:
    fileobj = tempfile.NamedTemporaryFile(dir=str(basedir), suffix='.tif')
    fileobj.close()
    try:
        yield fileobj.name
    finally:
        os.remove(fileobj.name)


TemporaryRasterFile = _named_tempfile

@click.command(
    'optimize-rasterstack',
    short_help='Optimize a collection of timeseries raster files for use with moja Flint.'
)

@click.argument('raster-pattern', type=RasterPattern(), required=True)
@click.option(
    '-n', '--raster-name', type=str,  required=True,
    help='Name of output stack'
)
@click.option(
    '-o', '--output-folder', required=True,
    type=PathlibPath(file_okay=False, writable=True),
    help='Output folder for blk files.'
)
@click.option(
    '--overwrite', is_flag=True, default=False, help='Force overwrite of existing files'
)

@click.option('-q', '--quiet', is_flag=True, default=False, show_default=True,
              help='Suppress all output to stdout')
def optimize_rasterstack(raster_pattern: RasterPatternType,
           raster_name: str,
           output_folder: Path,
           overwrite: bool = False,
           resampling_method: str = 'nearest',
           compression: str = 'auto',
           quiet: bool = False) -> None:
    """Optimize a collection of raster files for use with moja Flint.

    First argument is a list of input files or glob patterns.

    Example:

        $ flintdata optimize-rasterstack -o optimized/ -n Forest_Cover rasters/*.tif

    Note that all rasters may only contain a single band.
    """
    from rasterio import transform, windows

    keys, raster_files = raster_pattern
    raster_files_flat = [value for (key, value) in sorted(raster_files.items())]

    if not raster_files_flat:
        click.echo('No files given')
        return

    rs_method = RESAMPLING_METHODS[resampling_method]

    if compression == 'auto':
        compression = _prefered_compression_method()

    output_folder = Path(output_folder)
    output_folder.mkdir(exist_ok=True)

    total_pixels = 0;
    with rasterio.open(str(raster_files_flat[0]), 'r') as raster:
        raster_folder = output_folder / raster_name
        raster_folder.mkdir(exist_ok=True)

        _writeLayerInfo(raster, raster_name, raster_folder, len(raster_files_flat))
        west, south, east, north = raster.bounds
        # compute suggested resolution and bounds in target CRS
        dst_transform, _, _ = _calculate_default_transform(
            raster.crs, _TARGET_CRS, raster.width, raster.height, *raster.bounds
        )
        dst_res = (abs(dst_transform.a), abs(dst_transform.e))

        for tile in flintdata.flinttile.tiles(west, south, east, north):
            bounds = flintdata.flinttile.bounds(tile)
            dst_width = max(1, round((bounds[2] - bounds[0]) / dst_res[0]))
            dst_height = max(1, round((bounds[3] - bounds[1]) / dst_res[1]))
            total_pixels += dst_width * dst_height * len(raster_files_flat)

    if not quiet:
        # insert newline for nicer progress bar style
        click.echo('')

    with contextlib.ExitStack() as outer_env:
        pbar = outer_env.enter_context(tqdm.tqdm(
            total=total_pixels, smoothing=0, disable=quiet,
            bar_format='{l_bar}{bar}| [{elapsed}<{remaining}{postfix}]',
            desc='Building raster stacks'
        ))
        outer_env.enter_context(rasterio.Env(**GDAL_CONFIG))

        with contextlib.ExitStack() as file_env, warnings.catch_warnings():
            try:
                rasters = [file_env.enter_context(rasterio.open(f, 'r')) for f in raster_files_flat]
            except OSError as ex:
                print(ex)
                raise IOError('error while reading file.')

            for tile in flintdata.flinttile.tiles(west, south, east, north):
                with contextlib.ExitStack() as es, warnings.catch_warnings():
                    warnings.filterwarnings('ignore', message='invalid value encountered.*')

                    tile_bounds = flintdata.flinttile.bounds(tile)
                    # pad tile bounds to prevent interpolation artefacts
                    num_pad_pixels = 2

                    # compute tile VRT shape and transform
                    dst_width = max(1, round((tile_bounds[2] - tile_bounds[0]) / dst_res[0]))
                    dst_height = max(1, round((tile_bounds[3] - tile_bounds[1]) / dst_res[1]))
                    block_width = int(0.1 / dst_res[0])
                    block_height = int(0.1 / abs(dst_res[1]))
                    vrt_transform = (
                            transform.from_bounds(*tile_bounds, width=dst_width, height=dst_height)
                            * Affine.translation(-num_pad_pixels, -num_pad_pixels)
                    )
                    vrt_height, vrt_width = dst_height + 2 * num_pad_pixels, dst_width + 2 * num_pad_pixels

                    # remove padding in output
                    out_window = windows.Window(
                        col_off=num_pad_pixels, row_off=num_pad_pixels, width=dst_width, height=dst_height
                    )
                    tilelayers = list()
                    for layer in rasters:

                        # construct VRT
                        vrt = es.enter_context(
                            WarpedVRT(
                                layer, crs=_TARGET_CRS, resampling=rs_method,
                                transform=vrt_transform, width=vrt_width, height=vrt_height
                            )
                        )
                        with warnings.catch_warnings():
                            warnings.filterwarnings('ignore', message='invalid value encountered.*')
                            tile_data = vrt.read(
                                1, resampling=rs_method, window=out_window, out_shape=(dst_width, dst_height)
                            )
                            tilelayers.append(tile_data)
                        pbar.update(dst_width * dst_height)

                    blockedFileName = '{0}_{1}.blk'.format(raster_name, flintdata.flinttile.name(tile))

                    output_file = raster_folder / blockedFileName

                    if not overwrite and output_file.is_file():
                        raise click.BadParameter(
                            f'Output file {output_file!s} exists (use --overwrite to ignore)'
                        )

                    blocked_file = es.enter_context(open(output_file, "wb"))
                    tile_stack = np.stack(tilelayers, -1)

                    for row in range(0, dst_height, block_height):
                        for col in range(0, dst_width, block_width):
                            block = tile_stack[col:col+block_width, row:row+block_height, :]
                            b = bytes(block)  # python 3.n
                            blocked_file.write(b)




