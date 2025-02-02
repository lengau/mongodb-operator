#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import pytest
from pytest_operator.plugin import OpsTest


@pytest.fixture(scope="module")
async def application_charm(ops_test: OpsTest):
    """Build the application charm."""
    charm_path = "tests/integration/relation_tests/new_relations/application-charm"
    charm = await ops_test.build_charm(charm_path)
    return charm


@pytest.fixture(scope="module")
async def mongos_host_application_charm(ops_test: OpsTest):
    """Build the application charm."""
    charm_path = "tests/integration/sharding_tests/application"
    charm = await ops_test.build_charm(charm_path)
    return charm


@pytest.fixture(scope="module")
async def database_charm(ops_test: OpsTest):
    """Build the database charm."""
    charm = await ops_test.build_charm(".")
    return charm
