# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The module's own slice of the OpenAPI document — generated, never hand-maintained."""

import pytest
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.validation import validate_schema


@pytest.fixture(scope="module")
def schema() -> dict:
    return SchemaGenerator().get_schema(request=None, public=True)


def test_schema_is_valid(schema):
    validate_schema(schema)


def test_both_endpoints_are_documented(schema):
    assert set(schema["paths"]) == {"/api/lookup/v2/admin/search/", "/api/lookup/v2/admin/check/"}


def test_responses_reference_the_pydantic_schemas(schema):
    check = schema["paths"]["/api/lookup/v2/admin/check/"]["post"]
    assert check["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("CheckResponse")
    assert check["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("LookupQuery")
    assert set(check["responses"]) == {"200", "400", "401", "403", "429"}
