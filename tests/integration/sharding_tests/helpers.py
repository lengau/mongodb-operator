#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus

from pymongo import MongoClient
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, wait_fixed

from ..helpers import get_password
from ..relation_tests.new_relations.helpers import (
    get_application_relation_data,
    get_secret_data,
)

TIMEOUT = 10 * 60
MONGOS_PORT = 27018
MONGOD_PORT = 27017
SHARD_ONE_APP_NAME = "shard-one"
SHARD_TWO_APP_NAME = "shard-two"
CONFIG_SERVER_APP_NAME = "config-server"
CLUSTER_COMPONENTS = [SHARD_ONE_APP_NAME, SHARD_TWO_APP_NAME, CONFIG_SERVER_APP_NAME]
CONFIG_SERVER_REL_NAME = "config-server"
SHARD_REL_NAME = "sharding"


async def generate_mongodb_client(
    ops_test: OpsTest,
    app_name: str,
    mongos: bool,
    username: str = "operator",
    password: str = None,
):
    """Returns a MongoDB client for mongos/mongod."""
    hosts = [unit.public_address for unit in ops_test.model.applications[app_name].units]
    password = password or await get_password(ops_test, app_name=app_name)
    username = username
    port = MONGOS_PORT if mongos else MONGOD_PORT
    hosts = [f"{host}:{port}" for host in hosts]
    hosts = ",".join(hosts)
    auth_source = ""
    database = "admin"

    return MongoClient(
        f"mongodb://{username}:"
        f"{quote_plus(password)}@"
        f"{hosts}/{quote_plus(database)}?"
        f"{auth_source}"
    )


async def get_username_password(ops_test: OpsTest, app_name: str, relation_name: str) -> Tuple:
    secret_uri = await get_application_relation_data(
        ops_test, app_name, relation_name, "secret-user"
    )
    relation_user_data = await get_secret_data(ops_test, secret_uri)
    username = relation_user_data.get("username")
    password = relation_user_data.get("password")
    return (username, password)


def write_data_to_mongodb(client, db_name, coll_name, content) -> None:
    """Writes data to the provided collection and database."""
    db = client[db_name]
    horses_collection = db[coll_name]
    horses_collection.insert_one(content)


def verify_data_mongodb(client, db_name, coll_name, key, value) -> bool:
    """Checks a key/value pair for a provided collection and database."""
    db = client[db_name]
    test_collection = db[coll_name]
    query = test_collection.find({}, {key: 1})
    return query[0][key] == value


def get_cluster_shards(mongos_client) -> set:
    """Returns a set of the shard members."""
    shard_list = mongos_client.admin.command("listShards")
    curr_members = [member["host"].split("/")[0] for member in shard_list["shards"]]
    return set(curr_members)


def get_databases_for_shard(mongos_client, shard_name) -> Optional[List[str]]:
    """Returns the databases hosted on the given shard."""
    config_db = mongos_client["config"]
    if "databases" not in config_db.list_collection_names():
        return None

    databases_collection = config_db["databases"]

    if databases_collection is None:
        return

    return databases_collection.distinct("_id", {"primary": shard_name})


def has_correct_shards(mongos_client, expected_shards: List[str]) -> bool:
    """Returns true if the cluster config has the expected shards."""
    shard_names = get_cluster_shards(mongos_client)
    return shard_names == set(expected_shards)


def shard_has_databases(
    mongos_client, shard_name: str, expected_databases_on_shard: List[str]
) -> bool:
    """Returns true if the provided shard is a primary for the provided databases."""
    databases_on_shard = get_databases_for_shard(mongos_client, shard_name=shard_name)
    return set(databases_on_shard) == set(expected_databases_on_shard)


def count_users(mongos_client: MongoClient) -> int:
    """Returns the number of users using the cluster."""
    admin_db = mongos_client["admin"]
    users_collection = admin_db.system.users
    return users_collection.count_documents({})


async def deploy_cluster_components(
    ops_test: OpsTest, num_units_cluster_config: Dict = None
) -> None:
    if not num_units_cluster_config:
        num_units_cluster_config = {
            CONFIG_SERVER_APP_NAME: 2,
            SHARD_ONE_APP_NAME: 3,
            SHARD_TWO_APP_NAME: 1,
        }

    my_charm = await ops_test.build_charm(".")
    await ops_test.model.deploy(
        my_charm,
        num_units=num_units_cluster_config[CONFIG_SERVER_APP_NAME],
        config={"role": "config-server"},
        application_name=CONFIG_SERVER_APP_NAME,
    )
    await ops_test.model.deploy(
        my_charm,
        num_units=num_units_cluster_config[SHARD_ONE_APP_NAME],
        config={"role": "shard"},
        application_name=SHARD_ONE_APP_NAME,
    )
    await ops_test.model.deploy(
        my_charm,
        num_units=num_units_cluster_config[SHARD_TWO_APP_NAME],
        config={"role": "shard"},
        application_name=SHARD_TWO_APP_NAME,
    )

    await ops_test.model.wait_for_idle(
        apps=CLUSTER_COMPONENTS,
        idle_period=20,
        timeout=TIMEOUT,
    )


async def destroy_cluster(ops_test):
    """Destroy cluster in a forceful way."""
    for app in CLUSTER_COMPONENTS:
        await ops_test.model.applications[app].destroy(
            destroy_storage=True, force=True, no_wait=False
        )

    # destroy does not wait for applications to be removed, perform this check manually
    for attempt in Retrying(stop=stop_after_attempt(100), wait=wait_fixed(10), reraise=True):
        with attempt:
            # pytest_operator has a bug where the number of applications does not get correctly
            # updated. Wrapping the call with `fast_forward` resolves this
            async with ops_test.fast_forward():
                n_applications = len(ops_test.model.applications)
            # This case we don't raise an error in the context manager which
            # fails to restore the `update-status-hook-interval` value to it's former state.
            assert n_applications == 1, "old cluster not destroyed successfully."


async def integrate_cluster(ops_test: OpsTest) -> None:
    """Integrates the cluster components with each other."""
    await ops_test.model.integrate(
        f"{SHARD_ONE_APP_NAME}:{SHARD_REL_NAME}",
        f"{CONFIG_SERVER_APP_NAME}:{CONFIG_SERVER_REL_NAME}",
    )
    await ops_test.model.integrate(
        f"{SHARD_TWO_APP_NAME}:{SHARD_REL_NAME}",
        f"{CONFIG_SERVER_APP_NAME}:{CONFIG_SERVER_REL_NAME}",
    )
