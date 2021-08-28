"""Setup."""
from setuptools import setup, find_packages

# Parse the version from the pxmcli module.
with open("flintdata/__init__.py") as f:
    for line in f:
        if line.find("__version__") >= 0:
            version = line.split("=")[1].strip()
            version = version.strip('"')
            version = version.strip("'")
            continue

setup_reqs = ["numpy"]
inst_reqs = ["click>=6.7", "numpy>=1.15", "shapely", "rasterio>=1.0", "tqdm"]
extra_reqs = {"test": ["pytest", "pytest-cov", "codecov"]}

setup(
    name="flintdata",
    version=version,
    packages=find_packages(exclude=["ez_setup", "examples", "tests"]),
    python_requires=">=3",
    keywords="",
    url="https://github.com/moja-global/FLINT.data",
    classifiers=[
        "Intended Audience :: Information Technology",
        "Intended Audience :: Science/Research",
        "Programming Language :: Python :: 3.6",
        "Topic :: Scientific/Engineering :: GIS",
    ],
    author=u"Mal Francis",
    author_email="info@mulliongroup.com",
    license="",
    long_description=open("README.md").read(),
    setup_requires=setup_reqs,
    install_requires=inst_reqs,
    extras_require=extra_reqs,
    entry_points="""
        [console_scripts]
        flintdata=flintdata.scripts.cli:entrypoint
    """,
)
