# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""classify_inventory_type must trust a declared ``raw["inventory_type"]``
over guessing from ``ad_formats``/``sizes`` -- CTV rows have
``ad_formats=video`` but ``inventory_type=ctv``, and rate cards match on
this value by exact string, so guessing wrong silently changes pricing.
"""

import asyncio
from pathlib import Path

import pytest

from ad_seller.clients.ad_server_base import AdServerInventoryItem, AdServerType
from ad_seller.clients.csv_adapter import CSVAdServerClient
from ad_seller.services.catalog_service import classify_inventory_type

AWS_WORKSHOP_DIR = (
    Path(__file__).resolve().parent.parent.parent / "data" / "csv" / "samples" / "aws_workshop"
)


def _item(name: str, sizes=None, ad_formats=None, inventory_type=None) -> AdServerInventoryItem:
    """A CSV-shaped item: sizes on the base model, ad_formats/inventory_type
    stashed on ``raw`` the way ``CsvAdServerClient.list_inventory`` attaches
    every extra CSV column."""
    item = AdServerInventoryItem(
        id="inv-test",
        name=name,
        sizes=sizes or [],
        ad_server_type=AdServerType.CSV,
    )
    raw: dict = {"ad_formats": ad_formats or []}
    if inventory_type is not None:
        raw["inventory_type"] = inventory_type
    item.__dict__["raw"] = raw
    return item


def _gam_item(name: str, sizes=None) -> AdServerInventoryItem:
    """A GAM-shaped item: no ``raw`` attribute at all."""
    return AdServerInventoryItem(
        id="inv-test",
        name=name,
        sizes=sizes or [],
        ad_server_type=AdServerType.GOOGLE_AD_MANAGER,
    )


class TestNameStillWinsWhenInformative:
    """A name keyword must classify exactly as before, regardless of what
    ad_format/sizes say -- the fix is additive, never a regression."""

    def test_ctv_name_wins_over_conflicting_ad_format(self):
        item = _item("Connected TV Drama", sizes=[(300, 250)], ad_formats=["banner"])
        assert classify_inventory_type(item) == "ctv"

    def test_video_name_wins(self):
        item = _item("Preroll Video Slot", sizes=[(0, 0)], ad_formats=["banner"])
        assert classify_inventory_type(item) == "video"

    def test_native_name_wins(self):
        item = _item("Native Feed Unit", sizes=[(1920, 1080)], ad_formats=["video"])
        assert classify_inventory_type(item) == "native"

    def test_mobile_app_name_wins_over_video_ad_format(self):
        """Regression guard: 'Mobile App Interstitial' carries
        ad_formats=['banner', 'video'] in the real sample data -- the name
        match must still take priority over the new ad_format fallback."""
        item = _item(
            "Mobile App Interstitial",
            sizes=[(320, 480), (1080, 1920)],
            ad_formats=["banner", "video"],
        )
        assert classify_inventory_type(item) == "mobile_app"

    def test_linear_tv_name_wins(self):
        item = _item("SportsPulse Live Broadcasts", sizes=[(1920, 1080)], ad_formats=["video"])
        assert classify_inventory_type(item) == "linear_tv"


class TestAdFormatFallbackWhenNameIsUninformative:
    """Real rows from data/csv/samples/ whose name has no keyword match."""

    def test_native_ad_format_fallback(self):
        """data/csv/samples/web_display/inventory.csv: 'Article Inline',
        ad_formats=native, sizes=0x0 -- was misclassified as 'display'."""
        item = _item("Article Inline", sizes=[(0, 0)], ad_formats=["native"])
        assert classify_inventory_type(item) == "native"

    def test_video_ad_format_fallback(self):
        """data/csv/samples/aws_workshop/inventory.csv: 'Apex Premium
        Series', ad_formats=video, sizes=1920x1080 -- was 'display'."""
        item = _item("Apex Premium Series", sizes=[(1920, 1080)], ad_formats=["video"])
        assert classify_inventory_type(item) == "video"

    def test_hyphenated_preroll_name_falls_back_to_ad_format(self):
        """data/csv/samples/ctv_streaming/inventory.csv: 'Sports Pre-Roll
        :15/:30' doesn't contain the name matcher's unhyphenated 'preroll'
        substring, so this only resolves via the ad_format fallback."""
        item = _item("Sports Pre-Roll :15/:30", sizes=[(1920, 1080)], ad_formats=["video"])
        assert classify_inventory_type(item) == "video"

    def test_banner_ad_format_with_large_size_stays_display(self):
        """data/csv/samples/ctv_streaming/inventory.csv: 'Sports Pause Ad',
        ad_formats=banner, sizes=1920x1080 -- a big screen size alone must
        not be read as a CTV/video signal; ad_format still says banner."""
        item = _item("Sports Pause Ad", sizes=[(1920, 1080)], ad_formats=["banner"])
        assert classify_inventory_type(item) == "display"


class TestDeclaredInventoryTypeWinsOverGuessing:
    """6 of 10 rows in ctv_streaming/inventory.csv declare inventory_type=ctv
    but have hyphenated preroll/midroll names + ad_formats=video -- must
    classify as "ctv", not the guessed "video"."""

    @pytest.mark.parametrize(
        "name",
        [
            "Sports Pre-Roll :15/:30",
            "Sports Mid-Roll :15/:30",
            "News Pre-Roll :15/:30",
            "Entertainment Pre-Roll :30",
            "Entertainment Mid-Roll :15",
            "Kids & Family Pre-Roll :15",
        ],
    )
    def test_ctv_preroll_and_midroll_rows_classify_as_ctv_not_video(self, name):
        item = _item(name, sizes=[(1920, 1080)], ad_formats=["video"], inventory_type="ctv")
        assert classify_inventory_type(item) == "ctv"

    def test_declared_type_wins_even_against_a_conflicting_name_keyword(self):
        """Declared type outranks the name ladder too, not just ad_formats."""
        item = _item(
            "Video Highlights Reel",
            sizes=[(1920, 1080)],
            ad_formats=["video"],
            inventory_type="native",
        )
        assert classify_inventory_type(item) == "native"

    def test_unrecognised_declared_type_falls_back_instead_of_being_trusted_blindly(self):
        """An unrecognised declared value falls back to the ladder, not
        through verbatim."""
        item = _item(
            "Sports Pre-Roll :15/:30",
            sizes=[(1920, 1080)],
            ad_formats=["video"],
            inventory_type="premium-video-xyz",
        )
        assert classify_inventory_type(item) == "video"


class TestSizesFallbackAndGamCompatibility:
    """GAM/FreeWheel items never carry ``item.raw`` -- the fallback must
    still work off ``sizes`` alone without raising."""

    def test_gam_item_with_zero_sizes_and_no_other_signal_stays_display(self):
        """The '0x0 sizes -> native' rule is dropped: no real GAM inventory
        exercises it, and the sample row it did fire on already declares
        inventory_type=native."""
        item = _gam_item("Homepage Takeover", sizes=[(0, 0)])
        assert classify_inventory_type(item) == "display"

    def test_gam_item_with_real_sizes_and_no_name_signal_stays_display(self):
        item = _gam_item("Homepage Takeover", sizes=[(300, 250)])
        assert classify_inventory_type(item) == "display"

    def test_gam_item_with_no_sizes_at_all_stays_display(self):
        item = _gam_item("Homepage Takeover")
        assert classify_inventory_type(item) == "display"

    @pytest.mark.parametrize("name", ["Connected TV Drama", "Preroll Video Slot"])
    def test_gam_name_classification_unaffected(self, name):
        """No raw attribute at all must not raise for the name-matched path."""
        item = _gam_item(name, sizes=[(1920, 1080)])
        assert classify_inventory_type(item) in ("ctv", "video")


class TestAwsWorkshopVocabularyIsTrustedNotGuessed:
    """aws_workshop/inventory.csv declares ``linear``/``digital_video``/
    ``audio`` -- none of the original 6 recognised types. Before this fix
    those 8 rows had their declared value silently discarded and fell
    through inconsistently: 'SportsPulse Live Broadcasts' (linear) matched
    the name ladder's 'linear_tv' keyword, while its two sibling 'linear'
    rows had no name match and fell through the ad_formats=video fallback
    to 'video' instead -- two rows with an identical declared value ending
    up with different classifications. Each of the 3 declared values is
    now trusted verbatim, so siblings can no longer diverge."""

    @pytest.mark.parametrize(
        "name,declared",
        [
            ("GNN Primetime News", "linear"),
            ("SportsPulse Live Broadcasts", "linear"),
            ("Crestline Entertainment Block", "linear"),
            ("GNN.com Pre-Roll Video", "digital_video"),
            ("SportsPulse Mid-Roll Video", "digital_video"),
            ("GNN.com Outstream Video", "digital_video"),
            ("GNN Podcast Sponsorship", "audio"),
            ("Apex Companion Podcast Audio", "audio"),
        ],
    )
    def test_declared_value_is_trusted_verbatim(self, name, declared):
        item = _item(name, sizes=[(1920, 1080)], ad_formats=["video"], inventory_type=declared)
        assert classify_inventory_type(item) == declared

    def test_real_aws_workshop_csv_classifies_every_declared_type_verbatim(self):
        """Parses the real shipped CSV through CSVAdServerClient (not a
        synthetic item) so drift in the sample file itself would fail
        this test."""
        client = CSVAdServerClient(str(AWS_WORKSHOP_DIR))

        async def _load():
            await client.connect()
            return await client.list_inventory()

        items = asyncio.run(_load())
        by_id = {item.id: item for item in items}

        expected = {
            "inv-lin-gnn-primetime": "linear",
            "inv-lin-sportspulse-live": "linear",
            "inv-lin-crestline-entertainment": "linear",
            "inv-dig-gnn-preroll": "digital_video",
            "inv-dig-sportspulse-midroll": "digital_video",
            "inv-dig-gnn-outstream": "digital_video",
            "inv-aud-gnn-podcast": "audio",
            "inv-aud-apex-programmatic": "audio",
        }
        for item_id, declared in expected.items():
            assert item_id in by_id, f"{item_id} missing from aws_workshop/inventory.csv"
            assert classify_inventory_type(by_id[item_id]) == declared
