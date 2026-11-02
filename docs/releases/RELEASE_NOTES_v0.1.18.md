# VASCULUM v0.1.18

This release removes the LLM georeference curator. VASCULUM now consists of
the herbarium specimen collector and CheckList.

## Removed

- `llm_georeference_curator/`: estimating coordinates from label photographs
  with an LLM did not reach a quality usable for research (coarse placeholders
  kept as precise, very large radii for named areas, and high token cost).
- `run_collect_and_georeference.sh`, the combined collector-and-curator wrapper.
- The R requirement, which only served the curator's summary figure.

## Unchanged

- The collector still exports the coordinates supplied by each portal, with
  `geodeticDatum`, `coordinateUncertaintyInMeters`, `georeferenceProtocol`,
  `georeferenceSources` and related provenance when the portal provides them.
- `miss_` marking of misidentified specimens, image reuse and duplicate merging.
- CheckList (component version v0.1.2).

## Upgrading

Unzip into a new folder and run `bash setup_mac.sh`. Copy existing collector
outputs from `herbarium_specimen_collector/output/<taxon>/`; they are reused
without downloading the images again. The curator remains available in v0.1.17.
