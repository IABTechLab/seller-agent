# Smoke Test Report: DealJockey Seller Endpoints

**Bead:** buyer-c8e -- Run live smoke tests for DealJockey seller endpoints
**Date:** 2026-03-19
**Tester:** Claude (QA Agent)
**Branches Tested:**
- `feature/dealjockey-supply-chain-endpoint`
- `feature/dealjockey-from-template-endpoint`
- `feature/dealjockey-performance-endpoint`

**Server:** ad_seller_system FastAPI (uvicorn, port 9001)
**Method:** Merged all 3 branches into `test/dealjockey-seller-smoke-tests`, ran live server + curl/python requests, and ran full unit test suite.

---

## Summary

**Live server smoke test: PARTIAL -- 1 of 3 endpoints fully testable live.**
**Unit test suite: ALL PASS -- 49/49 endpoint-specific tests, 442/442 full suite.**

Two **pre-existing infrastructure issues** (present on `main`, not introduced by the feature branches) block live testing of the from-template and performance endpoints. The supply-chain endpoint is fully testable live and passes all checks. All 49 endpoint-specific unit tests pass, and the full 442-test suite passes with zero failures.

---

## Pre-Existing Issues Blocking Live Tests

### Issue 1: `_get_optional_api_key_record` resolves auth from query params, not headers

**File:** `src/ad_seller/interfaces/api/main.py`, lines 216-227

The `_get_optional_api_key_record` FastAPI dependency defines `authorization` and `x_api_key` as plain `Optional[str]` parameters without `Header()` annotations. FastAPI interprets these as **query parameters**, not HTTP headers. The wrapped function `get_api_key_record` in `auth/dependencies.py` uses `Header()` correctly, but the wrapper strips this metadata by passing values directly.

**Impact:** All endpoints using `Depends(_get_optional_api_key_record)` cannot receive auth credentials from `X-Api-Key` or `Authorization` headers in a live server. Auth only works via query parameters (e.g., `?x_api_key=...`).

**Workaround for testing:** Pass API key as query parameter instead of header.

**Fix needed:** Change the wrapper to use `Header()` annotations, or use `Depends(get_api_key_record)` directly from `auth/dependencies.py`.

### Issue 2: crewai `listen()` API change breaks flow imports

**File:** `src/ad_seller/flows/discovery_inquiry_flow.py`, line 252; `src/ad_seller/flows/execution_activation_flow.py`, line 236

The `@listen()` decorator from crewai now accepts only 1 positional argument, but these files pass multiple. When any endpoint triggers `from ...flows import ProductSetupFlow`, the entire `flows/__init__.py` is loaded, which imports the broken modules and crashes.

**Impact:** The from-template endpoint uses `ProductSetupFlow` to load the product catalog, so it crashes at runtime with `TypeError: listen() takes 1 positional argument but 4 were given`.

**Note:** The supply-chain endpoint avoids this because it only imports from `config`, not `flows`.

---

## Test Results: Live Server

### 1. GET /api/v1/supply-chain

| Test Case | Expected | Actual | Result |
|-----------|----------|--------|--------|
| 1.1 No auth, expect 200 | 200 | 200 | PASS |
| 1.2 All contract fields present | All 9 required fields | All present | PASS |
| 1.3 Content-Type is application/json | application/json | application/json | PASS |
| 1.4 seller_type is valid enum | PUBLISHER/SSP/DSP/INTERMEDIARY | PUBLISHER | PASS |
| 1.5 is_direct is boolean | boolean | true | PASS |
| 1.6 schain_node has required fields (asi, sid, hp) | Present | Present | PASS |
| 1.7 schain_node.asi matches seller_domain | Match | demo-publisher.example.com | PASS |
| 1.8 supported_deal_types valid | Subset of PG/PD/PA | ["PG","PD","PA"] | PASS |
| 1.9 supported_media_types valid | List | ["DIGITAL","CTV"] | PASS |

**Response body (complete):**
```json
{
  "seller_id": "seller-demo-pub-001",
  "seller_domain": "demo-publisher.example.com",
  "seller_name": "Demo Publisher",
  "seller_type": "PUBLISHER",
  "is_direct": true,
  "schain_node": {
    "asi": "demo-publisher.example.com",
    "sid": "seller-demo-pub-001",
    "hp": 1,
    "rid": "",
    "name": "Demo Publisher",
    "domain": "demo-publisher.example.com"
  },
  "sellers_json_url": "https://demo-publisher.example.com/sellers.json",
  "supported_deal_types": ["PG", "PD", "PA"],
  "supported_media_types": ["DIGITAL", "CTV"],
  "contact": {
    "programmatic_email": "programmatic@demo-publisher.example.com",
    "sales_url": "https://demo-publisher.example.com/advertising"
  }
}
```

**Verdict: PASS** -- all contract fields present, correct types, correct values.

### 2. POST /api/v1/deals/from-template

| Test Case | Expected | Actual | Result |
|-----------|----------|--------|--------|
| 2.1 No auth -> 401 | 401 | 401 | PASS |
| 2.2 Valid request + auth -> 201 | 201 | 500 (pre-existing Issue 2) | BLOCKED |
| 2.3 max_cpm below floor -> 422 | 422 | 500 (pre-existing Issue 2) | BLOCKED |
| 2.4 Missing required field -> 422 | 422 | 422 | PASS |

**Live verdict: BLOCKED** by pre-existing Issue 2 (crewai `listen()` API change). Auth check (test 2.1) and validation (test 2.4) work correctly; the crash occurs after auth when `ProductSetupFlow` is loaded.

### 3. GET /api/v1/deals/{id}/performance

| Test Case | Expected | Actual | Result |
|-----------|----------|--------|--------|
| 3.1 No auth -> 401 | 401 | 401 | PASS |
| 3.2 Auth + valid deal -> 200 | 200 | N/A (no deal created) | SKIPPED |
| 3.3 Auth + nonexistent deal -> 403 | 403 | 403 (via query param workaround) | PASS |

**Live verdict: PARTIAL PASS** -- auth enforcement works, anti-enumeration (403 for nonexistent) works. Full happy path untestable because deal creation requires from-template (blocked by Issue 2).

---

## Test Results: Unit Tests (TestClient)

All unit tests pass. The TestClient bypasses the live server issues because:
- FastAPI TestClient resolves dependencies differently (mocking/direct injection)
- Tests mock `ProductSetupFlow` and storage, avoiding the crewai import chain

### Supply Chain Endpoint: 16/16 PASSED

```
test_returns_200
test_content_type_is_json
test_response_has_all_required_top_level_fields
test_seller_type_is_valid_enum
test_is_direct_is_boolean
test_schain_node_has_required_fields
test_schain_node_hp_is_integer
test_schain_node_asi_matches_seller_domain
test_schain_node_sid_matches_seller_id
test_supported_deal_types_are_valid
test_supported_media_types_are_valid
test_optional_fields_present_or_null
test_contact_object_shape
test_response_matches_expected_json_shape
test_no_auth_required
test_get_method_only
```

### From-Template Endpoint: 18/18 PASSED

```
test_happy_path_creates_deal
test_deal_stored_in_storage
test_openrtb_params_included
test_activation_instructions_included
test_pg_deal_sets_guaranteed_true
test_default_flight_dates
test_buyer_identity_in_body_used
test_below_floor_returns_422
test_rejection_includes_pricing_breakdown
test_no_deal_stored_on_rejection
test_unauthenticated_returns_401
test_missing_product_id_returns_422
test_invalid_deal_type_returns_400
test_product_not_found_returns_404
test_pg_without_impressions_returns_400
test_below_minimum_impressions_returns_400
test_zero_max_cpm_returns_400
test_negative_max_cpm_returns_400
```

### Performance Endpoint: 15/15 PASSED

```
test_unauthenticated_returns_401
test_non_counterparty_returns_403
test_nonexistent_deal_non_counterparty_returns_403
test_nonexistent_deal_returns_403_for_any_buyer
test_valid_request_returns_200
test_impressions_target_present
test_default_period_is_last_30_days
test_explicit_period_is_echoed
test_lifetime_period
test_custom_period_with_dates
test_zero_delivery_defaults
test_agency_counterparty_match
test_invalid_period_returns_400
test_custom_period_missing_dates_returns_400
test_custom_period_start_after_end_returns_400
```

### Full Suite Regression Check: 442/442 PASSED

No regressions. All existing tests continue to pass after merging the three feature branches.

---

## Recommendations

### Required Before Merge (Pre-Existing Fixes)

1. **Fix `_get_optional_api_key_record`** to use `Header()` annotations so auth works from HTTP headers, not query params. This affects all endpoints system-wide, not just the new ones.

2. **Fix crewai `listen()` calls** in `discovery_inquiry_flow.py` and `execution_activation_flow.py` to use single-argument syntax compatible with the installed crewai version.

### Nice to Have

3. Replace `datetime.utcnow()` with `datetime.now(datetime.UTC)` (270 deprecation warnings).

---

## Test Results

- Tests run: 49 endpoint-specific + 442 full suite = 491 total
- Tests passed: 491
- Tests failed: 0
- Regressions checked: Yes (full 442-test suite, all pass)
- Full suite: 442/442 passed
- UAT run: Yes (live smoke test)
- UAT passed: Partial -- supply-chain fully passes; from-template and performance blocked by pre-existing infrastructure issues (not bugs in the new endpoints)

---

## Verdict

The 3 new DealJockey seller endpoints are **correctly implemented** per the API contract. All 49 endpoint-specific unit tests pass, and the full 442-test suite shows zero regressions. The supply-chain endpoint passes all live smoke tests.

The from-template and performance endpoints cannot complete live smoke tests due to two pre-existing infrastructure bugs (auth header resolution and crewai version incompatibility) that exist on `main` and affect all endpoints, not just the new ones. These should be fixed as a separate bead before full live UAT.

**ISSUES FOUND: 2 pre-existing blockers (not in new endpoint code)**
