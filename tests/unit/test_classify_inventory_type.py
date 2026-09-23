# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""classify_inventory_type is name-string-only; ignores placement ad_format
and sizes.

Regression tests: the classifier only ever looked at ``item.name``. Real
inventory rows whose name carries no classification keyword (e.g. "Article
Inline", "Apex Premium Series", "GNN Primetime News") fell straight through
to the "display" default even though their ``ad_formats``/``sizes`` clearly
identified them as native/video/etc. Scope is universal (any ad server): the
CSV adapter attaches ``ad_formats`` via ``item.raw``, GAM/FreeWheel items
carry no ``raw`` at all (only ``sizes``), so the fallback must degrade
gracefully rather than assume either is present.

Every case a name keyword already classified correctly is pinned here too,
to guard against the fix demoting a currently-correct classification (the
"Mobile App Interstitial" case: name says mobile_app, ad_formats includes
"video" -- name must still win).
"""

import pytest

from ad_seller.clients.ad_server_base import AdServerInventoryItem, AdServerType
from ad_seller.services.catalog_service import classify_inventory_type


def _item(name: str, sizes=None, ad_formats=None) -> AdServerInventoryItem:
    """A CSV-shaped item: sizes on the base model, ad_formats stashed on
    ``raw`` the way ``CsvAdServerClient.list_inventory`` attaches it."""
    item = AdServerInventoryItem(
        id="inv-test",
        name=name,
        sizes=sizes or [],
        ad_server_type=AdServerType.CSV,
    )
    item.__dict__["raw"] = {"ad_formats": ad_formats or []}
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


class TestSizesFallbackAndGamCompatibility:
    """GAM/FreeWheel items never carry ``item.raw`` -- the fallback must
    still work off ``sizes`` alone without raising."""

    def test_gam_item_with_zero_sizes_falls_back_to_native(self):
        item = _gam_item("Homepage Takeover", sizes=[(0, 0)])
        assert classify_inventory_type(item) == "native"

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
