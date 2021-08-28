"""scripts/optimize_rasters.py

Convert some raster files to optimized formats for use with moja flint.
"""

from typing import Sequence, Iterator, Union, Dict, Tuple, TypeVar
import os
import math
import warnings
import itertools
import contextlib
import tempfile
import logging
import json

from pathlib import Path

import click
import tqdm

import rasterio
from rasterio.shutil import copy
from rasterio.io import DatasetReader, MemoryFile
from rasterio.vrt import WarpedVRT
from rasterio.enums import Resampling
from rasterio.env import GDALVersion

from affine import Affine

import flintdata.flinttile

from flintdata.scripts.click_types import GlobbityGlob, PathlibPath

logger = logging.getLogger(__name__)

IN_MEMORY_THRESHOLD = 16000 * 16000

CACHEMAX = 1024 * 1024 * 512  # 512 MB

GDAL_CONFIG = {
    "GDAL_TIFF_INTERNAL_MASK": True,
    "GDAL_TIFF_OVR_BLOCKSIZE": 400,
    "GDAL_CACHEMAX": CACHEMAX,
    "GDAL_SWATH_SIZE": 2 * CACHEMAX,
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
}

COG_PROFILE = {
    "count": 1,
    "driver": "GTiff",
    "interleave": "pixel",
    "tiled": True,
    "blockxsize": 400,
    "blockysize": 400,
    "photometric": "MINISBLACK",
    "ZLEVEL": 1,
    "ZSTD_LEVEL": 9,
    "BIGTIFF": "IF_SAFER",
}

FLINT_TILE_PROFILE = {
    "count": 1,
    "driver": "GTiff",
    "interleave": "pixel",
    "tiled": True,
    "blockxsize": 400,
    "blockysize": 400,
    "width": 4000,
    "height": 4000,
    "BIGTIFF": "IF_SAFER",
}

RESAMPLING_METHODS = {
    "average": Resampling.average,
    "nearest": Resampling.nearest,
    "bilinear": Resampling.bilinear,
    "cubic": Resampling.cubic,
}

Number = TypeVar("Number", int, float)


# @staticmethod
def _calculate_default_transform(
    src_crs: Union[Dict[str, str], str],
    _TARGET_CRS: Union[Dict[str, str], str],
    width: int,
    height: int,
    *bounds: Number,
) -> Tuple[Affine, int, int]:
    """A more stable version of GDAL's default transform.

    Ensures that the number of pixels along the image's shortest diagonal remains
    the same in both CRS, without enforcing square pixels.

    Bounds are in order (west, south, east, north).
    """
    from rasterio import warp, transform

    if len(bounds) != 4:
        raise ValueError("Bounds must contain 4 values")

    # transform image corners to target CRS
    dst_corner_sw, dst_corner_nw, dst_corner_se, dst_corner_ne = list(
        zip(
            *warp.transform(
                src_crs,
                _TARGET_CRS,
                [bounds[0], bounds[0], bounds[2], bounds[2]],
                [bounds[1], bounds[3], bounds[1], bounds[3]],
            )
        )
    )

    # determine inner bounding box of corners in target CRS
    dst_corner_bounds = [
        max(dst_corner_sw[0], dst_corner_nw[0]),
        max(dst_corner_sw[1], dst_corner_se[1]),
        min(dst_corner_se[0], dst_corner_ne[0]),
        min(dst_corner_nw[1], dst_corner_ne[1]),
    ]

    # compute target resolution
    dst_corner_transform = transform.from_bounds(
        *dst_corner_bounds, width=width, height=height
    )
    target_res = (dst_corner_transform.a, dst_corner_transform.e)

    # get transform spanning whole bounds (not just projected corners)
    dst_bounds = warp.transform_bounds(src_crs, _TARGET_CRS, *bounds)
    dst_width = math.ceil((dst_bounds[2] - dst_bounds[0]) / target_res[0])
    dst_height = math.ceil((dst_bounds[1] - dst_bounds[3]) / target_res[1])
    dst_transform = transform.from_bounds(
        *dst_bounds, width=dst_width, height=dst_height
    )

    return dst_transform, dst_width, dst_height


def _prefered_compression_method() -> str:
    if not GDALVersion.runtime().at_least("2.3"):
        return "DEFLATE"

    # check if we can use ZSTD (fails silently for GDAL < 2.3)
    dummy_profile = dict(driver="GTiff", height=1, width=1, count=1, dtype="uint8")
    try:
        with MemoryFile() as memfile, memfile.open(compress="ZSTD", **dummy_profile):
            pass
    except Exception as exc:
        if "missing codec" not in str(exc):
            raise
    else:
        return "ZSTD"

    return "DEFLATE"


def _get_vrt(src: DatasetReader, rs_method: int) -> WarpedVRT:
    target_crs = "epsg:3857"
    vrt_transform, vrt_width, vrt_height = _calculate_default_transform(
        src.crs, target_crs, src.width, src.height, *src.bounds
    )
    vrt = WarpedVRT(
        src,
        crs=target_crs,
        resampling=rs_method,
        transform=vrt_transform,
        width=vrt_width,
        height=vrt_height,
        src_nodata=0,
        dst_nodata=255,
    )
    return vrt


_TARGET_CRS: str = "epsg:4326"


def _translate_type(dtype):
    return {
        rasterio.uint8: "UInt8",
        rasterio.uint16: "UInt16",
        rasterio.uint32: "UInt32",
        rasterio.int16: "Int16",
        rasterio.int32: "Int32",
        rasterio.float32: "Float32",
        rasterio.float64: "Float64",
    }[dtype]


def _writeLayerInfo(src, layerName, outFld, nLayers=None):
    info = _info(src)
    layerInfo = {
        "layer_type": "GridLayer",
        "layer_prefix": layerName,
        "layer_data": _translate_type(info["dtype"]),
        "tileLatSize": 1.0,
        "tileLonSize": 1.0,
        "blockLatSize": 0.1,
        "blockLonSize": 0.1,
        "cellLatSize": abs(info["transform"].d),
        "cellLonSize": info["transform"].a,
        "coordinateSystem": info["crs"],
        "cornerCoordinates": info["bounds"],
        "size": info["shape"],
    }
    if "nodata" in info:
        layerInfo["nodata"] = info["nodata"]
    if nLayers:
        layerInfo["nLayers"] = nLayers
        layerInfo["layer_type"] = "StackLayer"

    with open(os.path.join(outFld, layerName + ".json"), "w") as f:
        json.dump(layerInfo, f, ensure_ascii=False, sort_keys=True, indent=2)


def _info(src):
    info = dict(src.profile)
    info["shape"] = (info["height"], info["width"])
    info["bounds"] = src.bounds

    if src.crs:
        epsg = src.crs.to_epsg()
        if epsg:
            info["crs"] = "EPSG:{}".format(epsg)
        else:
            info["crs"] = src.crs.to_string()
    else:
        info["crs"] = None

    info["res"] = src.res
    info["colorinterp"] = [ci.name for ci in src.colorinterp]
    info["units"] = [units or None for units in src.units]
    info["descriptions"] = src.descriptions
    info["indexes"] = src.indexes
    info["mask_flags"] = [
        [flag.name for flag in flags] for flags in src.mask_flag_enums
    ]

    if src.crs:
        info["lnglat"] = src.lnglat()

    gcps, gcps_crs = src.gcps

    if gcps:
        info["gcps"] = {"points": [p.asdict() for p in gcps]}
        if gcps_crs:
            epsg = gcps_crs.to_epsg()
            if epsg:
                info["gcps"]["crs"] = "EPSG:{}".format(epsg)
            else:
                info["gcps"]["crs"] = src.crs.to_string()
        else:
            info["gcps"]["crs"] = None
    return info


@contextlib.contextmanager
def _named_tempfile(basedir: Union[str, Path]) -> Iterator[str]:
    fileobj = tempfile.NamedTemporaryFile(dir=str(basedir), suffix=".tif")
    fileobj.close()
    try:
        yield fileobj.name
    finally:
        os.remove(fileobj.name)


TemporaryRasterFile = _named_tempfile


@click.command(
    "optimize-rasters",
    short_help="Optimize a collection of raster files for use with moja Flint.",
)
@click.argument("raster-files", nargs=-1, type=GlobbityGlob(), required=True)
@click.option(
    "-o",
    "--output-folder",
    required=True,
    type=PathlibPath(file_okay=False, writable=True),
    help="Output folder for optimized rasters. Subdirectories will be flattened.",
)
@click.option(
    "--overwrite", is_flag=True, default=False, help="Force overwrite of existing files"
)
@click.option(
    "--resampling-method",
    type=click.Choice(RESAMPLING_METHODS.keys()),
    default="nearest",
    help="Resampling method for overviews",
    show_default=True,
)
@click.option(
    "--in-memory/--no-in-memory",
    default=None,
    help="Force processing raster in memory / not in memory [default: process in memory "
    f"if smaller than {IN_MEMORY_THRESHOLD // 1e6:.0f} million pixels]",
)
@click.option(
    "--compression",
    default="auto",
    type=click.Choice(["auto", "deflate", "lzw", "zstd", "none"]),
    help="Compression algorithm to use [default: auto (ZSTD if available, DEFLATE otherwise)]",
)
@click.option(
    "-q",
    "--quiet",
    is_flag=True,
    default=False,
    show_default=True,
    help="Suppress all output to stdout",
)
def optimize_rasters(
    raster_files: Sequence[Sequence[Path]],
    output_folder: Path,
    overwrite: bool = False,
    resampling_method: str = "nearest",
    in_memory: bool = None,
    compression: str = "auto",
    quiet: bool = False,
) -> None:
    """Optimize a collection of raster files for use with moja Flint.

    First argument is a list of input files or glob patterns.

    Example:

        $ flintdata optimize-rasters rasters/*.tif -o optimized/

    Note that all rasters may only contain a single band.
    """
    from rasterio import transform, windows

    raster_files_flat = sorted(set(itertools.chain.from_iterable(raster_files)))

    if not raster_files_flat:
        click.echo("No files given")
        return

    rs_method = RESAMPLING_METHODS[resampling_method]

    if compression == "auto":
        compression = _prefered_compression_method()

    total_pixels = 0
    for f in raster_files_flat:
        if not f.is_file():
            raise click.BadParameter(f"Input raster {f!s} is not a file")

        with rasterio.open(str(f), "r") as src:
            if src.count > 1 and not quiet:
                click.echo(
                    f"Warning: raster file {f!s} has more than one band. "
                    "Only the first one will be used.",
                    err=True,
                )
            total_pixels += src.height * src.width

    output_folder.mkdir(exist_ok=True)

    if not quiet:
        # insert newline for nicer progress bar style
        click.echo("")

    sub_pbar_args = dict(
        disable=quiet, leave=False, bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}"
    )

    with contextlib.ExitStack() as outer_env:
        pbar = outer_env.enter_context(
            tqdm.tqdm(
                total=total_pixels,
                smoothing=0,
                disable=quiet,
                bar_format="{l_bar}{bar}| [{elapsed}<{remaining}{postfix}]",
                desc="Optimizing rasters",
            )
        )
        outer_env.enter_context(rasterio.Env(**GDAL_CONFIG))

        for input_file in raster_files_flat:
            if len(input_file.name) > 30:
                short_name = input_file.name[:13] + "..." + input_file.name[-13:]
            else:
                short_name = input_file.name

            pbar.set_postfix(file=short_name)

            path = str(input_file)
            raster_name = os.path.splitext(os.path.basename(input_file.name))[0]

            with contextlib.ExitStack() as file_env, warnings.catch_warnings():
                try:
                    src = file_env.enter_context(rasterio.open(path))
                except OSError:
                    raise IOError("error while reading file {}".format(path))

                raster_folder = output_folder / raster_name
                raster_folder.mkdir(exist_ok=True)

                _writeLayerInfo(src, raster_name, raster_folder)

                # compute suggested resolution and bounds in target CRS
                dst_transform, _, _ = _calculate_default_transform(
                    src.crs, _TARGET_CRS, src.width, src.height, *src.bounds
                )
                dst_res = (abs(dst_transform.a), abs(dst_transform.e))

                west, south, east, north = src.bounds
                for tile in flintdata.flinttile.tiles(west, south, east, north):
                    with contextlib.ExitStack() as es, warnings.catch_warnings():
                        warnings.filterwarnings(
                            "ignore", message="invalid value encountered.*"
                        )

                        bounds = flintdata.flinttile.bounds(tile)

                        # pad tile bounds to prevent interpolation artefacts
                        num_pad_pixels = 2

                        # compute tile VRT shape and transform
                        dst_width = max(1, round((bounds[2] - bounds[0]) / dst_res[0]))
                        dst_height = max(1, round((bounds[3] - bounds[1]) / dst_res[1]))
                        vrt_transform = transform.from_bounds(
                            *bounds, width=dst_width, height=dst_height
                        ) * Affine.translation(-num_pad_pixels, -num_pad_pixels)
                        vrt_height, vrt_width = (
                            dst_height + 2 * num_pad_pixels,
                            dst_width + 2 * num_pad_pixels,
                        )

                        # remove padding in output
                        out_window = windows.Window(
                            col_off=num_pad_pixels,
                            row_off=num_pad_pixels,
                            width=dst_width,
                            height=dst_height,
                        )

                        # construct VRT
                        vrt = es.enter_context(
                            WarpedVRT(
                                src,
                                crs=_TARGET_CRS,
                                resampling=rs_method,
                                transform=vrt_transform,
                                width=vrt_width,
                                height=vrt_height,
                            )
                        )
                        profile = vrt.profile.copy()
                        profile.update(FLINT_TILE_PROFILE)

                        in_memory = vrt.width * vrt.height < IN_MEMORY_THRESHOLD

                        if in_memory:
                            mem_file = es.enter_context(MemoryFile())
                            dst = es.enter_context(mem_file.open(**profile))
                        else:
                            temp_raster = es.enter_context(
                                TemporaryRasterFile(basedir=raster_folder)
                            )
                            dst = es.enter_context(
                                rasterio.open(temp_raster, "w", **profile)
                            )

                        dst_transform = transform.from_bounds(
                            *bounds, width=dst_width, height=dst_height
                        ) * Affine.translation(+num_pad_pixels, +num_pad_pixels)

                        vrt_dst = es.enter_context(
                            WarpedVRT(
                                dst,
                                crs=_TARGET_CRS,
                                resampling=rs_method,
                                transform=dst_transform,
                                width=vrt_width,
                                height=vrt_height,
                            )
                        )

                        blockedFileName = "{0}_{1}.blk".format(
                            raster_name, flintdata.flinttile.name(tile)
                        )

                        output_file = raster_folder / blockedFileName

                        if not overwrite and output_file.is_file():
                            raise click.BadParameter(
                                f"Output file {output_file!s} exists (use --overwrite to ignore)"
                            )

                        blockedFile = es.enter_context(open(output_file, "wb"))

                        # iterate over blocks
                        block_windows = list(dst.block_windows(1))
                        for _, w in tqdm.tqdm(
                            block_windows, desc="Processing blocks", **sub_pbar_args
                        ):
                            out_window = windows.Window(
                                col_off=w.col_off + num_pad_pixels,
                                row_off=w.row_off + num_pad_pixels,
                                width=w.width,
                                height=w.height,
                            )
                            block_data = vrt.read(window=out_window, indexes=[1])
                            dst.write(block_data, window=w)
                            data = bytes(block_data)  # python 3.n
                            blockedFile.write(data)
                        output_file = raster_folder / "{0}_{1}.tif".format(
                            raster_name, flintdata.flinttile.index(tile)
                        )
                        copy(
                            dst,
                            str(output_file),
                            copy_src_overviews=True,
                            compress=compression,
                            **FLINT_TILE_PROFILE,
                        )

                        pbar.update(dst.height * dst.width)
