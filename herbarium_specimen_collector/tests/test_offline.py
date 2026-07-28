from __future__ import annotations

import sys
import csv
import io
import json
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from specimen_collector.html_utils import collect_image_candidates, collect_links
from specimen_collector.http_client import format_bytes
from specimen_collector.images import count_referenced_images, prune_unreferenced_images
from specimen_collector.models import SpecimenRecord
from specimen_collector.outputs import write_dwc_exports
from specimen_collector.pipeline import (
    collection_event_key,
    deduplicate,
    gbif_image_cache_urls,
    image_download_settings,
    normalize_source_names,
)
from specimen_collector.progress import TerminalProgress
from specimen_collector.records import image_basename, specimen_code
from specimen_collector.sources import (
    event_date_from_any,
    dwca_records,
    gbif_search_params,
    jacq_records,
    kag_records,
    nmnh_records,
    symbiota_records,
    tai2_records,
    ti_type_records,
    tns_webmuseum_records,
)


class FakeSymbiotaClient:
    def get_text(self, url: str, *, verify: bool | None = None) -> str:
        if "collections/list.php" in url:
            return """
            <input name="occid[]" value="1">
            <input name="occid[]" value="2">
            <input name="occid[]" value="3">
            """
        return "<html><title>record</title></html>"

    def get_json(self, url: str, *, verify: bool | None = None) -> list[dict[str, object]]:
        occid = url.rsplit("/", 1)[-1]
        if occid == "1":
            return [{"basisOfRecord": "HumanObservation", "sciname": "Haplopteris mediosora"}]
        institution = "NY" if occid == "3" else "MICH"
        return [
            {
                "basisOfRecord": "PreservedSpecimen",
                "sciname": "Haplopteris mediosora",
                "institutionCode": institution,
                "catalogNumber": f"{institution}{occid}",
                "recordedBy": "A. Collector",
                "eventDate": "1900-01-02",
                "country": "Taiwan",
                "locality": "Alishan",
            }
        ]

    def sleep(self, seconds: float) -> None:
        return None


class FakeTai2Client:
    def get_text(self, url: str, *, verify: bool | None = None) -> str:
        return """
        <script>
        var spcm = [
          {
            "TAIID":"271441",
            "herb":"台灣大學植物標本館(TAI)",
            "species":"Vittaria mediosora Hayata",
            "collno":"412",
            "note":"2700m. Tsuga forest",
            "type":"",
            "locinfo":{"loc":"鶯鶯峰","locE":"Yingyingfeng, Taiwan","district":"南投 Nantou","X":121.2325,"Y":24.1008,"country":"Taiwan"},
            "collinfo":"翁茂倫 Mao-Lun Weng",
            "date":"1998/12/25",
            "img":"/TAIimage/image/P25 Vittariaceae/Vittaria taeniophylla/271441.jpg",
            "label":"/TAIimage/label/P25 Vittariaceae/Vittaria taeniophylla/271441.jpg",
            "detinfo":{"collinfo":"郭城孟 Chen-Meng Kuo"}
          },
          {
            "TAIID":"254898",
            "herb":"台灣大學植物標本館(TAI)",
            "species":"Vittaria taeniophylla Copel.",
            "collno":"72",
            "locinfo":{"loc":"丹大林道","locE":"Tantai forest road, Taiwan","X":121.0,"Y":23.7,"country":"Taiwan"},
            "collinfo":"林均雅 Chuan-Ya Lin",
            "date":"2003/10/11",
            "img":"/TAIimage/image/P25 Vittariaceae/Vittaria taeniophylla/254898.jpg"
          }
        ];
        </script>
        """

    def sleep(self, seconds: float) -> None:
        return None


class FakeTnsClient:
    def get_text_with_url(self, url: str) -> tuple[str, str]:
        return (
            """
            <a href="javaScript:void(0)" onclick="window.open(&#39;https://db.kahaku.go.jp/webmuseum/detail?cls=col_b1_01&amp;pkey=TNS-VS,255550,&#39;, null, &#39;width=500&#39;);">255550</a>
            <img alt="画像" src="https://db.kahaku.go.jp/webmuseum/rest/media/S?cls=col_b1_01&amp;pkey=TNS-VS,255550,&amp;c2510">
            """,
            url,
        )

    def get_text(self, url: str) -> str:
        return """
        <input type="hidden" id="image_v" value="true">
        <table>
          <tr><th>標本登録番号 (TNS-VS-)</th><td>255550</td></tr>
          <tr><th>学名</th><td>Haplopteris mediosora (Hayata) X.C.Zhang</td></tr>
          <tr><th>採集地(国名)[英]</th><td>Japan</td></tr>
          <tr><th>採集地(都道府県名)[英]</th><td>Nagano Pref.</td></tr>
          <tr><th>採集地(都道府県名)[和]</th><td>長野県</td></tr>
          <tr><th>採集地(郡名)[英]</th><td>Minamisaku-gun</td></tr>
          <tr><th>採集地(郡名)[和]</th><td>南佐久郡</td></tr>
          <tr><th>採集地(市町村名)[英]</th><td>Kawakami-mura</td></tr>
          <tr><th>採集地(市町村名)[和]</th><td>川上村</td></tr>
          <tr><th>採集年月日</th><td>1968/6/11</td></tr>
          <tr><th>採集者名[英]</th><td>Shunsuke Serizawa</td></tr>
          <tr><th>採集番号</th><td>6047</td></tr>
          <tr><th>パーマネントリンク</th><td>https://db.kahaku.go.jp/webmuseum/col_b1_01/TNS-VS_255550</td></tr>
        </table>
        """

    def sleep(self, seconds: float) -> None:
        return None


class FakeJacqClient:
    def get_json(self, url: str, *, params: dict[str, object] | None = None, **kwargs: object) -> dict[str, object]:
        if "/images/list/" in url:
            return {
                "download": {
                    "europeana": [
                        "https://pictures.bgbm.org/iiif/3/B!20!01!60!16!B_20_0160165.jpg/full/1200,/0/default.jpg"
                    ],
                    "full": [
                        "https://pictures.bgbm.org/iiif/3/B!20!01!60!16!B_20_0160165.jpg/full/max/0/default.jpg"
                    ],
                }
            }
        return {
            "total": 1,
            "totalPages": 1,
            "result": [
                {
                    "dwc": {
                        "dwc:materialSampleID": "http://herbarium.bgbm.org/object/B200160165",
                        "dwc:basisOfRecord": "PreservedSpecimen",
                        "dwc:collectionCode": "B",
                        "dwc:catalogNumber": "B 20 0160165",
                        "dwc:scientificName": "Pteris vittata L.",
                        "dwc:country": "Yemen",
                        "dwc:locality": "Socotra",
                        "dwc:eventDate": "2002-03-23",
                        "dwc:recordedBy": "Kilian,N.",
                        "dwc:fieldNumber": "YP 2064",
                    },
                    "jacq": {
                        "jacq:stableIdentifier": "http://herbarium.bgbm.org/object/B200160165",
                        "jacq:specimenID": 931868,
                        "jacq:OwnerOrganizationAbbrev": "B",
                        "jacq:LicenseURI": "http://creativecommons.org/licenses/by-sa/3.0/",
                    },
                }
            ],
        }

    def sleep(self, seconds: float) -> None:
        return None


class FakeTiClient:
    def get_text(self, url: str, **kwargs: object) -> str:
        if "Detail/detail.php" not in url:
            return "<form></form>"
        return """
        <a target="enlarged_image" onclick="javescript:disp('Pteridaceae/TI00203898')">
          <img src="/DImages/Shokubutsu/herbarium_ferns/Type/Pteridaceae/TI00203898_0128_.jpg">
        </a>
        <table>
          <tr><td>TI CODE</td><td>TI00203898</td></tr>
          <tr><td>Scientific Name</td><td>Vittaria&nbsp; mediosora&nbsp; Hayata</td></tr>
          <tr><td>Type Status</td><td>lectotype</td></tr>
          <tr><td>Family</td><td>Pteridaceae</td></tr>
          <tr><td>Locality</td><td>TAIWAN: Formosa: Mt. Arisan</td></tr>
          <tr><td>Collector</td><td>S. Sasaki s.n.</td></tr>
          <tr><td>Collection Date</td><td>Mar. [May] 1913</td></tr>
        </table>
        """

    def post_text_with_url(self, url: str, data: dict[str, object] | None = None, **kwargs: object) -> tuple[str, str]:
        return (
            """
            <a href="../Detail/detail.php?No=203898&-tokenproject=Keyword&-langTop=jp">TI00203898</a>
            <em>Vittaria&nbsp; mediosora&nbsp; Hayata</em>
            <img src="/DImages/Shokubutsu/herbarium_ferns/Type/Pteridaceae/TI00203898_0032_.jpg">
            """,
            url,
        )

    def sleep(self, seconds: float) -> None:
        return None


class FakeNmnhClient:
    def get_text(self, url: str, **kwargs: object) -> str:
        return "<html>NMNH search</html>"

    def post_json(self, url: str, data: dict[str, object] | None = None, **kwargs: object) -> dict[str, object]:
        return {
            "recordsFetched": 1,
            "records": [
                {
                    "_id": 12336854,
                    "admuu": "04324c87ea23412eb75257918838724d",
                    "biopr": ["Topping, D. L."],
                    "biopn": "654",
                    "catbc": "01482068",
                    "catct": "Pteridophytes",
                    "catnb": {"catnc": "854020"},
                    "coldv": {"coled": "1907-02-17", "colvd": "17 Feb 1907"},
                    "darct": "Philippines",
                    "darcx": "US 854020",
                    "daric": "US",
                    "darlc": "Guadalupe, Rizal Prov., Luzon",
                    "darsn": "Pteris vittata",
                    "idefa": {"ideqn": "Pteris vittata L.", "ideib": "Det. Person"},
                    "mulmm": [
                        {
                            "detrg": "National Museum of Natural History, Smithsonian Institution",
                            "detrs": "CC0",
                            "mulid": 11233737,
                            "mulmt": "image",
                            "muluu": "be4033c86dc9441cba0c094ee69b6493",
                            "siids": True,
                        }
                    ],
                }
            ],
        }

    def sleep(self, seconds: float) -> None:
        return None


class FakeKagClient:
    def post_text(self, url: str, data: dict[str, object] | None = None, **kwargs: object) -> str:
        data = data or {}
        if data.get("opt") == "view_collect":
            return """
            <table class='norm'>
              <tr><th colspan=2>Label information#1</th></tr>
              <tr><th>specimen_id</th><td class='norm'>KAG022683</td></tr>
              <tr><th>collector number</th><td class='norm'>123</td></tr>
              <tr><th>collector date</th><td class='norm'>2019/10/31</td></tr>
              <tr><th>collector name</th><td class='norm'>Suzuki Eizi</td></tr>
              <tr><th>country</th><td class='norm'>Japan</td></tr>
              <tr><th>prefecture</th><td class='norm'>Kagoshima Amami Islands Uke Shima Isl.</td></tr>
              <tr><th>locality</th><td class='norm'>Lon=129.26 Lat=28.01</td></tr>
              <tr><th>note</th><td class='norm'>Alt. 101m</td></tr>
            </table>
            <table>
              <tr>
                <td title='view image'><a href='picture/KAG022683/KAG022683.jpg'>KAG022683.jpg</a></td>
                <td>(831.4Kbyte)</td>
              </tr>
              <tr>
                <td title='view image'><a href='picture/KAG022683/KAG022683P.jpg'>KAG022683P.jpg</a></td>
                <td>(605.7Kbyte)</td>
              </tr>
            </table>
            """
        return """
        <table class='norm'>
          <tr>
            <th abbr='id'>#</th>
            <th abbr='S_specimen_id'>specimen_id</th>
            <th abbr='S_family'>Family</th>
            <th abbr='S_genus'>Genus</th>
            <th abbr='S_epithet'>Epithet</th>
            <th abbr='S_type_kind'>Type_kind</th>
            <th abbr='S_Collection_Site'>Collection_Site</th>
            <th abbr='S_japanese_name'>japanese_name</th>
            <th abbr='S_collector_name'>collector_name</th>
          </tr>
          <tr>
            <td class='norm'><a name='TXNDET0000'>1</a></td>
            <td class='norm'>KAG022683</td>
            <td class='norm'>Pteridaceae</td>
            <td class='norm'>Pteris</td>
            <td class='norm'>vittata</td>
            <td class='norm'></td>
            <td class='norm'>Japan, Kagoshima Amami Islands</td>
            <td class='norm'>モエジマシダ</td>
            <td class='norm'>Suzuki Eizi</td>
            <td class='norm'>
              <form method='post' action='/musedb/s_plant/s_plant.php#TXNDET-001'>
                <input type='hidden' name='opt' value='view_collect'>
                <input type='hidden' name='specimen_id' value='KAG022683'>
              </form>
            </td>
          </tr>
        </table>
        <table><tr><td>Total record number 1 : list up from 1 to 1<br></td></tr></table>
        """

    def sleep(self, seconds: float) -> None:
        return None


class FakeDwcaClient:
    def get_bytes(self, url: str, **kwargs: object) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr(
                "occurrence.txt",
                "\t".join(
                    [
                        "id",
                        "type",
                        "basisOfRecord",
                        "occurrenceID",
                        "recordNumber",
                        "recordedBy",
                        "associatedMedia",
                        "eventDate",
                        "country",
                        "stateProvince",
                        "locality",
                        "decimalLatitude",
                        "decimalLongitude",
                        "scientificName",
                    ]
                )
                + "\n"
                + "\t".join(
                    [
                        "BO:1347954",
                        "Holotype",
                        "PreservedSpecimen",
                        "BO:1347954",
                        "4236",
                        "Bunnemeijer, H.A.B.",
                        "https://data.brin.go.id/api/access/datafile/153279",
                        "1919-08",
                        "Indonesia",
                        "Sumatra Utara",
                        "G. Malintang",
                        "0.9747",
                        "99.537517",
                        "Arachniodes puncticulata",
                    ]
                )
                + "\n",
            )
            zf.writestr(
                "multimedia.txt",
                "id\ttype\tformat\tidentifier\ttitle\tpublisher\tlicense\trightsHolder\n"
                "BO:1347954\tStillImage\timage/jpeg\thttps://data.brin.go.id/api/access/datafile/153279\tArachniodes puncticulata\tBRIN\tCC-BY-NC\tHerbarium Bogoriense\n",
            )
        return buffer.getvalue()

    def sleep(self, seconds: float) -> None:
        return None


def main() -> None:
    settings = json.loads((ROOT / "config" / "source_settings.json").read_text())
    assert len(settings["enabled_sources"]) == 24
    assert not {"k", "p", "hast"}.intersection(settings["enabled_sources"])
    assert [part["component_name"] for part in settings["mo"]["components"]] == [
        "pteridoportal_mo"
    ]
    standard = image_download_settings(settings, "standard")
    low = image_download_settings(settings, "low")
    assert (standard["max_image_dimension"], standard["jpeg_quality"]) == (2400, 88)
    assert (low["max_image_dimension"], low["jpeg_quality"]) == (1600, 84)

    for columns in (32, 50, 80):
        terminal = TerminalProgress(
            ["gbif", "pteridoportal", "taif"],
            stream=io.StringIO(),
            terminal_width=columns,
        )
        terminal.update_source(
            "gbif",
            status="processing",
            completed=1,
            total=2,
            records=1234,
            images=9,
        )
        terminal.set_task(
            "gbif - searching a deliberately long synonym that must be truncated"
        )
        progress_lines = terminal._lines()
        assert all(len(line) <= columns - 1 for line in progress_lines)
        assert progress_lines[0].startswith("Sources: 3 selected")
        assert sum(line.startswith("gbif") for line in progress_lines) == 1

    cvh_html = '''
    <a href="/spms/detail.php?id=abc123">record</a>
    <a href="/spms/detail.php?id=def456">record</a>
    '''
    links = collect_links(cvh_html, "https://www.cvh.ac.cn/spms/list.php", [__import__("re").compile(r"detail\.php\?id=[A-Za-z0-9_-]+")])
    assert len(links) == 2

    image_html = '''
    <img src="/static/logo.png">
    <a href="https://mediaphoto.mnhn.fr/media/12345">Image</a>
    <img data-large="https://example.org/specimens/PE01234567.jpg">
    <a href="https://images.ala.org.au/image/proxyImageThumbnailLarge?imageId=abc">AVH image</a>
    <a href="https://data.nhm.ac.uk/media/aa-bb-cc/contents">NHM image</a>
    <img src="https://i.creativecommons.org/l/by/3.0/88x31.png">
    '''
    images = collect_image_candidates(image_html, "https://science.mnhn.fr/item/1")
    assert "https://mediaphoto.mnhn.fr/media/12345" in images
    assert "https://example.org/specimens/PE01234567.jpg" in images
    assert "https://images.ala.org.au/image/proxyImageThumbnailLarge?imageId=abc" in images
    assert "https://data.nhm.ac.uk/media/aa-bb-cc/contents" in images
    assert not any("logo" in url for url in images)
    assert not any("creativecommons" in url for url in images)

    records = [
        SpecimenRecord(source="gbif", occurrence_id="x", image_url="https://a/1.jpg"),
        SpecimenRecord(source="gbif", occurrence_id="x", image_url="https://a/1.jpg"),
        SpecimenRecord(source="gbif", occurrence_id="x", image_url="https://a/2.jpg"),
        SpecimenRecord(source="cvh", source_record_url="https://b/detail?id=2"),
    ]
    assert len(deduplicate(records)) == 2
    assert format_bytes(1024) == "1.0 KB"
    assert event_date_from_any(-2024956800000, 1905, 11, "") == "1905-11"

    assert normalize_source_names(["GBIF", "UC/JEPS"]) == [
        "gbif",
        "ucjeps",
    ]

    same_physical = [
        SpecimenRecord(source="gbif", institution_code="MNHN", catalog_number="P01187634", image_url="https://x/1.jpg"),
        SpecimenRecord(source="p", institution_code="P", catalog_number="P01187634", source_record_url="https://y/record"),
    ]
    merged = deduplicate(same_physical)
    assert len(merged) == 1
    assert merged[0].merged_record_count == "2"
    assert merged[0].merged_from_sources == "gbif; p"

    named_record = SpecimenRecord(
        institution_code="TNS",
        catalog_number="TNS-VS-255550",
        scientific_name="Vittaria mediosora Hayata",
        type_status="Holotype",
    )
    assert specimen_code(named_record) == "TNSVS255550"
    assert (
        image_basename(named_record, "Haplopteris mediosora")
        == "TNSVS255550_Haplopteris_mediosora_holotype"
    )
    rbge_record = SpecimenRecord(
        institution_code="RBGE",
        collection_code="E",
        catalog_number="E01539315",
    )
    assert specimen_code(rbge_record) == "E01539315"

    with tempfile.TemporaryDirectory() as tmpdir:
        export_dir = Path(tmpdir)
        named_record.local_image_path = (
            "images/TNSVS255550_Haplopteris_mediosora_holotype.jpg"
        )
        write_dwc_exports(
            export_dir,
            [named_record],
            "Haplopteris mediosora",
        )
        with (export_dir / "dwc.csv").open(encoding="utf-8", newline="") as handle:
            csv_row = next(csv.DictReader(handle))
        with (export_dir / "dwc.tsv").open(encoding="utf-8", newline="") as handle:
            tsv_row = next(csv.DictReader(handle, delimiter="\t"))
        assert csv_row == tsv_row
        assert csv_row["catalogNumber"] == "TNSVS255550"
        assert csv_row["otherCatalogNumbers"] == "TNS-VS-255550"
        assert named_record.local_image_path in csv_row["associatedMedia"]

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir)
        image_dir = output_dir / "images"
        image_dir.mkdir()
        referenced_image = image_dir / "referenced.jpg"
        unreferenced_image = image_dir / "unreferenced.jpg"
        referenced_image.write_bytes(b"referenced")
        unreferenced_image.write_bytes(b"unreferenced")
        image_record = SpecimenRecord(local_image_path="images/referenced.jpg")
        duplicate_reference = SpecimenRecord(local_image_path="images/referenced.jpg")
        assert (
            count_referenced_images(
                output_dir,
                [image_record, duplicate_reference],
            )
            == 1
        )
        assert prune_unreferenced_images(output_dir, [image_record]) == 1
        assert referenced_image.exists()
        assert not unreferenced_image.exists()

    same_image = [
        SpecimenRecord(source="gbif", image_url="https://example.org/specimens/shared.jpg"),
        SpecimenRecord(source="cnh", image_url="https://example.org/specimens/shared.jpg"),
    ]
    assert len(deduplicate(same_image)) == 1

    same_event = [
        SpecimenRecord(
            source="gbif",
            institution_code="K",
            catalog_number="K0001",
            recorded_by="A. Collector",
            record_number="123",
            event_date="1900-01-02",
            country="Taiwan",
            locality="Alishan",
        ),
        SpecimenRecord(
            source="gbif",
            institution_code="P",
            catalog_number="P0001",
            recorded_by="A. Collector",
            record_number="123",
            event_date="1900-01-02",
            country="Taiwan",
            locality="Alishan",
        ),
    ]
    event_unique = deduplicate(same_event)
    assert len(event_unique) == 2
    assert collection_event_key(event_unique[0])
    assert event_unique[0].collection_event_key == event_unique[1].collection_event_key

    with tempfile.TemporaryDirectory() as tmpdir:
        symbiota = symbiota_records(
            client=FakeSymbiotaClient(),  # type: ignore[arg-type]
            source="pteridoportal",
            query_name="Haplopteris mediosora",
            raw_dir=Path(tmpdir),
            settings={
                "base_url": "https://example.org/portal",
                "specimens_only": True,
                "exact_name_filter": True,
                "request_delay_seconds": 0,
            },
            max_records=2,
            record_offset=0,
            refresh=True,
        )
    assert [record.catalog_number for record in symbiota] == ["MICH2", "NY3"]

    with tempfile.TemporaryDirectory() as tmpdir:
        ny_only = symbiota_records(
            client=FakeSymbiotaClient(),  # type: ignore[arg-type]
            source="ny",
            query_name="Haplopteris mediosora",
            raw_dir=Path(tmpdir),
            settings={
                "base_url": "https://example.org/portal",
                "specimens_only": True,
                "exact_name_filter": True,
                "institution_codes": ["NY"],
                "request_delay_seconds": 0,
            },
            max_records=2,
            record_offset=0,
            refresh=True,
        )
    assert [record.catalog_number for record in ny_only] == ["NY3"]

    with tempfile.TemporaryDirectory() as tmpdir:
        tai_records = tai2_records(
            client=FakeTai2Client(),  # type: ignore[arg-type]
            source="tai",
            query_name="Haplopteris mediosora",
            raw_dir=Path(tmpdir),
            settings={
                "species_pages": {"Haplopteris mediosora": ["https://tai2.ntu.edu.tw/species/example"]},
                "name_equivalents": {"Haplopteris mediosora": ["Haplopteris mediosora", "Vittaria mediosora"]},
                "exact_name_filter": True,
                "request_delay_seconds": 0,
            },
            max_records=None,
            record_offset=0,
            refresh=True,
        )
    assert len(tai_records) == 1
    assert tai_records[0].catalog_number == "271441"
    assert tai_records[0].scientific_name == "Vittaria mediosora Hayata"
    assert tai_records[0].event_date == "1998-12-25"
    assert tai_records[0].elevation == "2700 m"
    assert tai_records[0].image_url.endswith("/TAIimage/image/P25%20Vittariaceae/Vittaria%20taeniophylla/271441.jpg")

    with tempfile.TemporaryDirectory() as tmpdir:
        tns_records = tns_webmuseum_records(
            client=FakeTnsClient(),  # type: ignore[arg-type]
            query_name="Haplopteris mediosora",
            raw_dir=Path(tmpdir),
            settings={"request_delay_seconds": 0, "page_size": 10, "max_pages_per_name": 1},
            max_records=None,
            record_offset=0,
            refresh=True,
        )
    assert len(tns_records) == 1
    assert tns_records[0].catalog_number == "TNS-VS-255550"
    assert tns_records[0].event_date == "1968-06-11"
    assert tns_records[0].recorded_by == "Shunsuke Serizawa"
    assert "Nagano Pref." in tns_records[0].locality

    with tempfile.TemporaryDirectory() as tmpdir:
        jacq = jacq_records(
            client=FakeJacqClient(),  # type: ignore[arg-type]
            source="b",
            query_name="Pteris vittata",
            raw_dir=Path(tmpdir),
            settings={
                "institution_codes": ["B"],
                "page_size": 10,
                "request_delay_seconds": 0,
                "with_images_only": True,
                "preferred_image_size": "europeana",
            },
            max_records=None,
            record_offset=0,
            refresh=True,
        )
    assert len(jacq) == 1
    assert jacq[0].catalog_number == "B 20 0160165"
    assert "/1200,/" in jacq[0].image_url

    with tempfile.TemporaryDirectory() as tmpdir:
        ti_records = ti_type_records(
            client=FakeTiClient(),  # type: ignore[arg-type]
            query_name="Vittaria mediosora",
            raw_dir=Path(tmpdir),
            settings={"request_delay_seconds": 0, "page_size": 25, "max_pages_per_name": 1},
            max_records=None,
            record_offset=0,
            refresh=True,
        )
    assert len(ti_records) == 1
    assert ti_records[0].catalog_number == "TI00203898"
    assert ti_records[0].type_status == "lectotype"
    assert ti_records[0].country == "TAIWAN"
    assert "TI00203898_2048_.jpg" in ti_records[0].image_url

    with tempfile.TemporaryDirectory() as tmpdir:
        nmnh = nmnh_records(
            client=FakeNmnhClient(),  # type: ignore[arg-type]
            source="us",
            query_name="Pteris vittata",
            raw_dir=Path(tmpdir),
            settings={"request_delay_seconds": 0, "page_size": 10, "max_pages_per_name": 1, "image_width": 1600},
            max_records=None,
            record_offset=0,
            refresh=True,
        )
    assert len(nmnh) == 1
    assert nmnh[0].catalog_number == "US 854020"
    assert nmnh[0].record_number == "654"
    assert "ids.si.edu/ids/deliveryService" in nmnh[0].image_url

    with tempfile.TemporaryDirectory() as tmpdir:
        kag = kag_records(
            client=FakeKagClient(),  # type: ignore[arg-type]
            source="kag",
            query_name="Pteris vittata",
            raw_dir=Path(tmpdir),
            settings={"request_delay_seconds": 0, "page_size": 10, "max_pages_per_name": 1},
            max_records=None,
            record_offset=0,
            refresh=True,
        )
    assert len(kag) == 1
    assert kag[0].catalog_number == "KAG022683"
    assert kag[0].event_date == "2019-10-31"
    assert kag[0].decimal_latitude == "28.01"
    assert kag[0].decimal_longitude == "129.26"
    assert kag[0].elevation == "101 m"
    assert kag[0].image_url.endswith("/picture/KAG022683/KAG022683.jpg")

    with tempfile.TemporaryDirectory() as tmpdir:
        bo_dwca = dwca_records(
            client=FakeDwcaClient(),  # type: ignore[arg-type]
            source="bo",
            query_name="Arachniodes puncticulata",
            raw_dir=Path(tmpdir),
            settings={
                "request_delay_seconds": 0,
                "institution_code": "BO",
                "collection_code": "BO",
                "archives": [{"name": "pteridophyte", "archive_url": "https://example.org/archive.zip"}],
            },
            max_records=None,
            record_offset=0,
            refresh=True,
        )
    assert len(bo_dwca) == 1
    assert bo_dwca[0].institution_code == "BO"
    assert bo_dwca[0].catalog_number == "BO:1347954"
    assert bo_dwca[0].type_status == "Holotype"
    assert bo_dwca[0].decimal_latitude == "0.9747"
    assert bo_dwca[0].image_url == "https://data.brin.go.id/api/access/datafile/153279"
    assert (
        image_basename(bo_dwca[0], "Arachniodes puncticulata")
        == "BO1347954_Arachniodes_puncticulata_holotype"
    )

    params = gbif_search_params(
        "Haplopteris mediosora",
        {"occurrence_mode": "specimens", "coordinate_filter": "with-coordinates"},
        100,
        0,
    )
    assert params["basisOfRecord"] == "PRESERVED_SPECIMEN"
    assert params["hasCoordinate"] == "true"
    assert params["hasGeospatialIssue"] == "false"

    cache_record = SpecimenRecord(
        source="gbif",
        source_record_id="3019930399",
        image_url="https://mediaphoto.mnhn.fr/media/1622034116665jRZ0iepwxyLTP4eE",
    )
    cache_urls = gbif_image_cache_urls(cache_record)
    assert cache_urls[0].endswith("/media/bdd5ff1595941645a86762eb3e81fc28")
    print("Offline tests passed.")


if __name__ == "__main__":
    main()
