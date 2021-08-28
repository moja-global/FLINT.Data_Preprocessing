User insights on spatially explicit forest carbon budget model workflow
=======================================================================

This document provides workflow considerations for spatially explicit
modelling of forest carbon budgets using the Generic Carbon Budget Model
(GCBM) and FLINT (Full Lands INtegration Tool). The comments herein are
based on personal experience using the tools to model various projects
in Canada, ranging from the development of historic carbon stock and
flux inventories to forward-looking mitigation analyses that explore
forest management scenarios aimed as reducing net greenhouse gas (GHG)
emissions.

Model areas range from several thousand to several million hectares at
resolutions of 1 km2 to 1 hectare, or even 0.25 hectares. Project areas
have typically been focused in British Columbia, Canada, but have
included projects in other provinces such as Ontario and New Brunswick.

Projects from different jurisdictions and with different objectives may
encounter entirely different challenges than those that our team has
encountered thus far. That said, the techniques and learnings presented
in this document may prove useful as suggestions and starting points for
new users.

In partnership with various other agencies around the world, including
major contributions from Australian colleagues, the Canadian Forest
Service (CFS) is developing a spatially explicit forest carbon budget
model. The model utilizes the `moja global`_ Full Lands Integration Tool
(FLINT) and incorporates the science of the existing and widely used,
spatially referenced Carbon Budget Model for the Canadian Forest Sector
`CBM-CFS3`_. As FLINT is a generic integration tool, the model in
development is termed the “generic” carbon budget model (GCBM).

At CFS, GCBM currently consists of a collection of scripts and
executables that are utilized to prepare input data such as forest
inventory and disturbance history, pass that input data into the
GCBM/FLINT carbon budget model, and then organize result outputs.

.. image:: ../_static/images/GCBM-high-level-overview.png
   :alt: GCBM-high-level-overview

Each stage of this process can be further broken down into smaller
steps, which are described in the following sections.

.. _moja global: http://moja.global/
.. _CBM-CFS3: http://www.nrcan.gc.ca/forests/climate-change/carbon-accounting/13107


.. toctree::
   :hidden:

   input-data.rst
   data-preparation
   gcbm-data-objectives
   summary
