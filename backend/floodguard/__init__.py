"""FloodGuard India — dam-break inundation modelling framework.

Package layout mirrors the physical pipeline:
    data/       acquisition + caching of DEM, dam, river, exposure layers
    preprocess/ DEM conditioning, grids, reservoir geometry, cross-sections
    breach/     breach parameter models, growth, outflow, reservoir routing
    engines/    hydrodynamic solvers behind one Engine interface
    postprocess/derived rasters, polygonisation, GIS exports
    impact/     HADR exposure analysis
    compare/    cross-engine quantitative comparison
    validation/ analytical + benchmark tests
"""

__version__ = "0.1.0"
