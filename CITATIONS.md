# Data Sources

Primary public services used by the configured adapters:

- [GBIF Occurrence API](https://techdocs.gbif.org/en/openapi/v1/occurrence)
- [PteridoPortal](https://www.pteridoportal.org/portal/)
- [Consortium of Northeastern Herbaria](https://neherbaria.org/portal/)
- [CCH2 / Consortium of California Herbaria](https://cch2.org/portal/)
- [Atlas of Living Australia / AVH](https://avh.ala.org.au/)
- [Chinese Virtual Herbarium](https://www.cvh.ac.cn/spms/list.php)
- [JBRJ / Reflora web services](https://servicos.jbrj.gov.br/v2/jabot/swagger.json)
- [Natural History Museum Data Portal](https://data.nhm.ac.uk/)
- [Naturalis Netherlands Biodiversity API](https://api.biodiversitydata.nl/v2/)
- [Royal Botanic Garden Edinburgh herbarium data](https://data.rbge.org.uk/search/herbarium/)
- [Plants of Taiwan / TAI](https://tai2.ntu.edu.tw/)
- [TAIF](https://taif.tfri.gov.tw/)
- [TNS WebMuseum](https://db.kahaku.go.jp/)

Georeferencing and environmental context services:

- [OpenStreetMap contributors](https://www.openstreetmap.org/copyright)
- [Overpass API](https://wiki.openstreetmap.org/wiki/Overpass_API)
- [Open-Meteo Elevation API](https://open-meteo.com/en/docs/elevation-api)
- [Copernicus DEM](https://doi.org/10.5270/ESA-c5d3d65)
- [Natural Earth](https://www.naturalearthdata.com/) v5.1.2 source tag, used for
  conservative offline geographic screening; public domain. Original file URLs
  and hashes are recorded in llm_georeference_curator/data/geographic_screen.json.

LLM-assisted runs should also record and cite the selected provider, model,
and access date. These values are retained in the curator outputs and logs.

Methodological reference for LLM-assisted georeferencing:

- Xie, Y., Park, D. S., Sinnott-Armstrong, M. A., et al. (2025).
  Using large language models to address the bottleneck of georeferencing
  natural history collections. Nature Plants, 11, 2446-2450.
  https://doi.org/10.1038/s41477-025-02162-y

For research outputs, cite each underlying dataset and institution using the
information in the DwC export and source record page.
Occurrence API retrieval does not create a GBIF download DOI; obtain and cite a
GBIF download DOI separately when the publication requires one. Image rights
and occurrence-data licenses are recorded separately and can differ.

Georeferencing guidance and scientific plotting:

- [GBIF Georeferencing Best Practices](https://docs.gbif.org/georeferencing-best-practices/1.0/en/).
- Brunson, J. C. (2020). ggalluvial: Layered Grammar for Alluvial Plots.
  Journal of Open Source Software, 5(49), 2017. https://doi.org/10.21105/joss.02017
- Cite R and ggplot2 using `citation()` and `citation("ggplot2")` in the R
  environment used to render the figure. Use `citation("ggalluvial")` for the
  installed package citation. The bundled R source is the sole ribbon renderer.
- Coordinate transformations use [pyproj/PROJ](https://pyproj4.github.io/pyproj/stable/).
  Runtime versions and the transformation operation are recorded in curator audit outputs.
- Geographic geometry operations use [Shapely](https://shapely.readthedocs.io/).
  Shapely/GEOS versions are recorded in the implementation signature.

CheckList outputs also include provenance tables for the GBIF, CVH, and
OpenStreetMap records used to build the visual checklist. See
`checklist/CITATIONS.md` for the pipeline-specific citation notes.
