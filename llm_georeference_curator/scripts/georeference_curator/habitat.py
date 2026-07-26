from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, Sequence, Tuple, Union


@dataclass(frozen=True)
class HabitatDefinition:
    key: str
    description: str
    aliases: Tuple[str, ...] = ()
    expected_classes: Tuple[str, ...] = ()
    avoided_classes: Tuple[str, ...] = ()
    elevation_floor_meters: int = 0
    elevation_ceiling_meters: int = 0
    wilderness: bool = False


def definition(
    key: str,
    description: str,
    *,
    aliases=(),
    expected=(),
    avoided=(),
    floor=0,
    ceiling=0,
    wilderness=False,
) -> HabitatDefinition:
    return HabitatDefinition(
        key=key,
        description=description,
        aliases=tuple(aliases),
        expected_classes=tuple(expected),
        avoided_classes=tuple(avoided),
        elevation_floor_meters=floor,
        elevation_ceiling_meters=ceiling,
        wilderness=wilderness,
    )


HABITAT_DEFINITIONS: Dict[str, HabitatDefinition] = {
    item.key: item
    for item in (
        definition("city", "urban or built-up habitat", aliases=("urban", "built-up"), expected=("built_up",)),
        definition("ruderal", "disturbed ground and waste places", aliases=("disturbed", "waste ground"), expected=("built_up", "agriculture")),
        definition("roadside", "road verge or transport corridor", aliases=("road verge",), expected=("transport", "built_up")),
        definition("agricultural", "farmland or cultivated ground", aliases=("farmland", "cropland"), expected=("agriculture",)),
        definition("paddy field", "flooded rice field", aliases=("rice field", "paddy"), expected=("agriculture", "wetland")),
        definition("plantation", "managed tree plantation", expected=("forest", "agriculture")),
        definition("orchard", "orchard or perennial crop", expected=("agriculture",)),
        definition("forest", "forest or closed woodland", aliases=("woodland",), expected=("forest",), avoided=("built_up",), wilderness=True),
        definition("lowland forest", "low-elevation forest", expected=("forest",), avoided=("built_up",), ceiling=1200, wilderness=True),
        definition("temperate forest", "temperate forest", expected=("forest",), avoided=("built_up",), wilderness=True),
        definition("boreal forest", "boreal or taiga forest", aliases=("taiga",), expected=("forest",), avoided=("built_up",), wilderness=True),
        definition("tropical rainforest", "humid tropical rainforest", aliases=("rainforest",), expected=("forest",), avoided=("built_up", "desert"), wilderness=True),
        definition("dry forest", "seasonally dry forest", expected=("forest", "shrubland"), avoided=("built_up",), wilderness=True),
        definition("cloud forest", "humid montane cloud forest", expected=("forest",), avoided=("built_up", "desert"), floor=400, wilderness=True),
        definition("montane forest", "mountain forest", expected=("forest",), avoided=("built_up",), floor=400, wilderness=True),
        definition("subalpine forest", "upper-montane or subalpine forest", aliases=("sub-alpine forest",), expected=("forest",), avoided=("built_up",), floor=600, wilderness=True),
        definition("alpine", "alpine zone above treeline", aliases=("alpine meadow",), expected=("grassland", "bare_rock", "snow_ice"), avoided=("built_up",), floor=800, wilderness=True),
        definition("shrubland", "shrub-dominated vegetation", aliases=("scrub",), expected=("shrubland",), avoided=("built_up",), wilderness=True),
        definition("heathland", "heath or ericaceous shrubland", aliases=("heath",), expected=("heath", "shrubland"), avoided=("built_up",), wilderness=True),
        definition("grassland", "natural or semi-natural grassland", expected=("grassland",), avoided=("built_up",)),
        definition("meadow", "meadow", expected=("grassland",)),
        definition("savanna", "savanna", expected=("grassland", "shrubland", "forest"), avoided=("built_up",), wilderness=True),
        definition("steppe", "steppe", expected=("grassland", "shrubland"), avoided=("built_up",), wilderness=True),
        definition("tundra", "tundra", expected=("moss_lichen", "grassland", "shrubland", "snow_ice"), avoided=("built_up",), wilderness=True),
        definition("wetland", "wetland", expected=("wetland", "water"), avoided=("built_up",)),
        definition("marsh", "marsh", expected=("wetland", "water"), avoided=("built_up",)),
        definition("swamp", "forested or wooded swamp", expected=("wetland", "forest", "water"), avoided=("built_up",)),
        definition("bog", "ombrotrophic bog", expected=("wetland", "peatland"), avoided=("built_up",)),
        definition("fen", "minerotrophic fen", expected=("wetland", "peatland"), avoided=("built_up",)),
        definition("peatland", "peat-forming wetland", aliases=("peat bog",), expected=("peatland", "wetland"), avoided=("built_up",)),
        definition("riparian", "riverbank or streamside habitat", aliases=("riverine", "streamside"), expected=("river", "wetland"), avoided=("built_up",)),
        definition("river", "river channel or river margin", expected=("river", "water")),
        definition("stream", "stream, creek, or ravine watercourse", aliases=("creek", "brook", "ravine"), expected=("river", "water")),
        definition("spring", "freshwater spring or seep", aliases=("seep", "seepage"), expected=("spring", "water", "wetland")),
        definition("waterfall spray zone", "waterfall or persistent spray zone", aliases=("waterfall", "spray zone"), expected=("waterfall", "river", "water")),
        definition("lake", "lake shore or lake habitat", expected=("standing_water", "water")),
        definition("pond", "pond margin or pond habitat", expected=("standing_water", "water")),
        definition("freshwater aquatic", "submerged or floating freshwater habitat", aliases=("freshwater", "aquatic"), expected=("water", "river", "standing_water")),
        definition("brackish", "brackish water", expected=("estuary", "coast", "water"), ceiling=100),
        definition("estuary", "estuary", expected=("estuary", "coast", "water"), ceiling=100),
        definition("mangrove", "mangrove forest", expected=("mangrove", "wetland", "coast"), avoided=("built_up",), ceiling=50),
        definition("salt marsh", "coastal salt marsh", expected=("wetland", "coast"), ceiling=50),
        definition("sea", "marine habitat", aliases=("marine", "ocean"), expected=("marine", "coast", "water"), avoided=("built_up",), ceiling=100),
        definition("seagrass meadow", "marine seagrass bed", aliases=("seagrass",), expected=("marine", "coast", "water"), ceiling=20),
        definition("intertidal", "intertidal shore", aliases=("tidal",), expected=("coast", "marine", "wetland"), ceiling=30),
        definition("rocky shore", "rocky coast", expected=("coast", "bare_rock"), ceiling=100),
        definition("beach", "sandy or shingle beach", expected=("beach", "coast", "sand"), ceiling=100),
        definition("coastal dune", "coastal sand dune", aliases=("coastal sand dune",), expected=("sand", "coast"), ceiling=150),
        definition("desert", "desert", expected=("desert", "sand", "bare_rock"), avoided=("forest", "wetland"), wilderness=True),
        definition("semi-desert", "semi-arid desert margin", aliases=("semidesert",), expected=("desert", "shrubland", "bare_rock"), avoided=("wetland",), wilderness=True),
        definition("inland dune", "inland sand dune", aliases=("sand dune", "dune"), expected=("sand", "desert"), avoided=("wetland",), wilderness=True),
        definition("limestone", "limestone substrate", aliases=("calcareous",), expected=("limestone", "karst", "bare_rock")),
        definition("karst", "karst landscape", expected=("karst", "limestone", "cave", "bare_rock")),
        definition("ultramafic", "ultramafic substrate", aliases=("serpentine", "serpentinite"), expected=("ultramafic", "bare_rock")),
        definition("volcanic", "volcanic substrate", aliases=("lava",), expected=("volcanic", "bare_rock")),
        definition("geothermal", "geothermal ground or hot spring", aliases=("hot spring",), expected=("geothermal", "spring", "bare_rock")),
        definition("gypsum", "gypsum substrate", expected=("gypsum", "bare_rock")),
        definition("saline", "saline inland soil or salt flat", aliases=("salt flat",), expected=("saline", "bare_rock")),
        definition("rock outcrop", "rock outcrop", aliases=("rocky outcrop", "bare rock"), expected=("bare_rock",)),
        definition("cliff", "cliff face", expected=("cliff", "bare_rock")),
        definition("scree", "scree or talus slope", aliases=("talus",), expected=("scree", "bare_rock")),
        definition("cave", "cave entrance or cave habitat", expected=("cave", "karst")),
        definition("epiphytic", "tree-trunk or canopy epiphyte microhabitat", aliases=("epiphyte",), expected=("forest",)),
        definition("canopy", "forest canopy", expected=("forest",)),
        definition("soil", "terrestrial soil", expected=()),
        definition("rock crevice", "rock crevice microhabitat", expected=("bare_rock", "cliff")),
    )
}


@dataclass(frozen=True)
class HabitatPreference:
    canonical: Tuple[str, ...] = ()
    custom: Tuple[str, ...] = ()
    original: Tuple[str, ...] = ()

    @property
    def enabled(self) -> bool:
        return bool(self.canonical or self.custom)

    @property
    def display(self) -> str:
        return " | ".join((*self.canonical, *self.custom))

    @property
    def definitions(self) -> Tuple[HabitatDefinition, ...]:
        return tuple(HABITAT_DEFINITIONS[key] for key in self.canonical)

    @property
    def expected_classes(self) -> Tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                value
                for item in self.definitions
                for value in item.expected_classes
            )
        )

    @property
    def avoided_classes(self) -> Tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                value
                for item in self.definitions
                for value in item.avoided_classes
            )
        )

    @property
    def elevation_floor_meters(self) -> int:
        return max(
            (item.elevation_floor_meters for item in self.definitions),
            default=0,
        )

    @property
    def elevation_ceiling_meters(self) -> int:
        ceilings = [
            item.elevation_ceiling_meters
            for item in self.definitions
            if item.elevation_ceiling_meters > 0
        ]
        return min(ceilings, default=0)

    @property
    def wilderness(self) -> bool:
        return any(item.wilderness for item in self.definitions)

    def as_prompt_payload(self) -> dict:
        return {
            "input": list(self.original),
            "canonicalHabitats": list(self.canonical),
            "customTerms": list(self.custom),
            "ecologicalDescriptions": [
                item.description for item in self.definitions
            ],
            "expectedMapClasses": list(self.expected_classes),
            "avoidedMapClasses": list(self.avoided_classes),
            "softElevationFloorMeters": self.elevation_floor_meters or None,
            "softElevationCeilingMeters": self.elevation_ceiling_meters or None,
        }


def normalize_habitat_text(value: str) -> str:
    return " ".join(
        re.sub(r"[_-]+", " ", str(value or "").strip().casefold()).split()
    )


def alias_map() -> Dict[str, str]:
    aliases = {}
    for key, item in HABITAT_DEFINITIONS.items():
        for alias in (key, *item.aliases):
            aliases[normalize_habitat_text(alias)] = key
    return aliases


def flatten_habitat_values(values: Union[Sequence[str], str]) -> Iterable[str]:
    if isinstance(values, str):
        values = (values,)
    for value in values:
        for part in re.split(r"[,;|]", str(value or "")):
            cleaned = part.strip()
            if cleaned:
                yield cleaned


def parse_habitats(values: Union[Sequence[str], str]) -> HabitatPreference:
    aliases = alias_map()
    canonical = []
    custom = []
    original = list(flatten_habitat_values(values))
    ordered_aliases = sorted(aliases, key=len, reverse=True)
    for raw in original:
        normalized = normalize_habitat_text(raw)
        direct = aliases.get(normalized)
        if direct:
            canonical.append(direct)
            continue
        matched = []
        for alias in ordered_aliases:
            if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", normalized):
                matched.append(aliases[alias])
        if matched:
            canonical.extend(matched)
        custom.append(raw)
    return HabitatPreference(
        canonical=tuple(dict.fromkeys(canonical)),
        custom=tuple(dict.fromkeys(custom)),
        original=tuple(original),
    )


def habitat_vocabulary() -> Tuple[str, ...]:
    return tuple(HABITAT_DEFINITIONS)
