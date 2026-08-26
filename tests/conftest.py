# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import pytest
from django.test import override_settings

from django_lookup.providers import registry


@pytest.fixture
def fake_provider():
    """Registry pointed at tests.fake_provider; cache cleared around the test."""
    from tests import fake_provider as module

    registry.clear_cache()
    module.reset()
    with override_settings(LOOKUP_PROVIDERS={"fake": "tests.fake_provider"}):
        yield module
    module.reset()
    registry.clear_cache()


@pytest.fixture
def wired_provider(settings):
    """`tests.signal_provider` registered as `pim_product` with its freshness signals connected."""
    from django_lookup import signals

    settings.LOOKUP_PROVIDERS = {"pim_product": "tests.signal_provider"}
    registry.clear_cache()
    signals.connect()
    yield
    signals.disconnect()
    registry.clear_cache()


@pytest.fixture
def eager_celery():
    """Run `.delay()` inline so a signal's task is observable inside the test transaction."""
    from celery import current_app

    previous = current_app.conf.task_always_eager
    current_app.conf.task_always_eager = True
    yield
    current_app.conf.task_always_eager = previous


@pytest.fixture
def pim_provider(settings):
    """`tests.fake_provider` registered as the `pim_product` catalog (the API scope default)."""
    from tests import fake_provider as module

    registry.clear_cache()
    module.reset()
    settings.LOOKUP_PROVIDERS = {"pim_product": "tests.fake_provider"}
    yield module
    module.reset()
    registry.clear_cache()


@pytest.fixture(autouse=True)
def fake_embedding():
    """Every test starts with a healthy, deterministic embedding backend."""
    from tests import fake_embedding as module

    module.reset()
    yield module
    module.reset()


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def admin_client(api_client, django_user_model):
    from rest_framework.test import APIClient

    user = django_user_model.objects.create_superuser(username="admin", email="admin@test.local", password="pass")
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def customer_client(django_user_model):
    """A storefront customer: authenticated, but no business in an admin endpoint."""
    from rest_framework.test import APIClient

    user = django_user_model.objects.create_user(username="customer", password="pass")
    client = APIClient()
    client.force_authenticate(user=user)
    return client
