"""cli."""
import logging
from typing import Any, Mapping
import sys

import click

from flintdata import __version__, logs
from flintdata.scripts.optimize_rasters import optimize_rasters
from flintdata.scripts.optimize_rasterstack import optimize_rasterstack


@click.group(short_help="Command line interface for flintdata")
@click.version_option(version=__version__, message="%(version)s")
def cogbuilder():
    """Command line interface for flintdata."""
    pass


@click.group("flintdata", invoke_without_command=True)
@click.option(
    "--loglevel",
    help="Set level for log messages",
    default=None,
    type=click.Choice(["debug", "info", "warning", "error", "critical"]),
)
@click.version_option(version=__version__)
@click.pass_context
def cli(
    ctx: click.Context, config: Mapping[str, Any] = None, loglevel: str = None
) -> None:
    """The command line interface for flintdata.
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())

    # setup logging
    #    settings = get_settings()

    if loglevel is None:
        loglevel = "debug"  # settings.LOGLEVEL

    logs.set_logger(loglevel, catch_warnings=True)


def entrypoint() -> None:
    try:
        cli(obj={})
    except Exception:
        logger = logging.getLogger(__name__)
        logger.exception("Uncaught exception!", exc_info=True)
        sys.exit(1)


cli.add_command(optimize_rasters)
cli.add_command(optimize_rasterstack)

if __name__ == "__main__":
    entrypoint()
