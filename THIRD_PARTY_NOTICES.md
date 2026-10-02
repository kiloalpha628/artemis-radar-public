# Third-party notices

The [MIT license](LICENSE) covers this project's code and documentation. It does not replace dependency licenses, upstream data terms, or third-party trademark rights.

## Py-ART

This project uses `arm_pyart==2.2.5` for Level II decoding, velocity dealiasing, and radar utilities. Py-ART is distributed under a BSD-style license with an Argonne/U.S. Government notice. The complete, unmodified notice for the pinned release is in [licenses/Py-ART.txt](licenses/Py-ART.txt), copied from [upstream v2.2.5](https://github.com/ARM-DOE/pyart/blob/v2.2.5/LICENSE.txt).

Retain that notice, its conditions, and disclaimer when redistributing Py-ART source. When redistributing Py-ART binaries, reproduce them in documentation or accompanying materials. Do not claim endorsement by Argonne, UChicago Argonne, the U.S. Government, or contributors. Clearly mark modifications to Py-ART itself. This project installs the upstream package; it does not vendor or modify its source.

The Docker image includes this document, the project license, and `licenses/Py-ART.txt` under `/app`. Keep the installed packages' own license files as well: Py-ART and the other direct/transitive dependencies retain their respective notices. This file is not an exhaustive dependency-license inventory; review the exact installed packages when redistributing a built image.

For research using Py-ART, its developers request citation of Helmus, J. J. and Collis, S. M. (2016), *The Python ARM Radar Toolkit (Py-ART), a Library for Working with Weather Radar Data in the Python Programming Language*, Journal of Open Research Software, 4(1), e25, [doi:10.5334/jors.119](https://doi.org/10.5334/jors.119).

## Radar data and fixtures

Radar observations originate from NOAA/NWS NEXRAD and are distributed through the [public Unidata archive](https://registry.opendata.aws/noaa-nexrad/). Identify NOAA/NWS as the source, avoid implying endorsement, and identify locally processed products as such; see the [NWS data-use policy](https://www.weather.gov/disclaimer). The project MIT license does not claim copyright over underlying U.S. Government radar observations.

Committed fixtures include locally processed NOAA/NWS observations and explicitly labelled synthetic examples. [fixtures/sources.json](fixtures/sources.json) records the archive objects, and [fixtures/provenance.json](fixtures/provenance.json) records processing settings and checksums. Keep those records with redistributed fixture samples.

## Open-Meteo

Storm-relative velocity optionally uses [Open-Meteo](https://open-meteo.com/) forecast winds. The [API data license](https://open-meteo.com/en/licence) is [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Give credit, link to the license, and identify changes. Clients displaying derived storm-relative products should link to Open-Meteo beside the display and explain that forecast winds were transformed into a Bunkers right-mover estimate and subtracted from radial velocity.

The configured free API endpoint is restricted to non-commercial use under the [service terms](https://open-meteo.com/en/terms). Set `STORM_RELATIVE_VELOCITY=false` to avoid those requests. This implementation does not support configuring a paid endpoint or API key. It calls the API and does not redistribute Open-Meteo's server source code.

## Reflectivity palette

The `Standard` palette approximates a RadarScope color scale sampled from screenshots of its color bar; see `palettes.py` for provenance. RadarScope is a third-party product. No affiliation, endorsement, or license to its trademarks is implied. That provenance does not establish permission to reuse any third-party protected material. Confirm reuse rights before publishing the sampled palette and generated fixtures; the `NWSRef` option changes future rendering but does not replace committed `Standard` fixtures.
