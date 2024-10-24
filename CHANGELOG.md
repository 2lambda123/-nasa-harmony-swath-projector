## v1.0.1
### 2024-04-05

This version of the Swath Projector implements black code formatting across the
entire repository. There should be no functional changes to the service.

## v1.0.0
### 2023-11-16

This version of the Harmony Swath Projector contains all functionality
previously released internally to EOSDIS as `sds/swot-reproject:0.0.4`.
Minor reformatting of the repository structure has occurred to comply with
recommended best practices for a Harmony backend service repository, but the
service itself is functionally unchanged. Additional contents to the repository
include updated documentation and files outlined by the
[NASA open-source guidelines](https://code.nasa.gov/#/guide).

Repository structure changes include:

* Migrating `pymods` directory to `swath_projector`.
* Migrating `swotrepr.py` to `swath_projector/adapter.py`.
* Addition of `swath_projector/main.py`.

For more information on internal releases prior to NASA open-source approval,
see legacy-CHANGELOG.md.
