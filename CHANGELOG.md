## v0.0.2
### 2022-07-11

This version of the Swath Projector updates the `harmony-service-lib`
dependency to v1.0.20, to accommodate change in the way Harmony handles STAC
objects. Other dependencies are also updated, including the version of Python
in which the service is run (now 3.9).

## v0.0.1
### 2022-01-05

This version of the Swath Projector implements semantic version numbers to
allow for tagging of Docker images, and better control of different Docker
images in different environments.

The basic functionality in this service offers projection of swath data into
a projected grid. Interpolation options include:

* Nearest Neighbour
* Bilinear
* Elliptically Weighted Averaging (EWA)
* EWA-Nearest Neighbour (EWA-NN)

Interpolation is accomplished using the [pyresample](https://pyresample.readthedocs.io/en/latest/)
Python package.