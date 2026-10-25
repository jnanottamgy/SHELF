"""The harness's choice of database: never a hosted server unless asked for by name."""

from tests.conftest import COMPOSE_TEST_DATABASE_URL, _derive_test_database_url

LOCAL = "postgresql+psycopg://u:p@localhost:5432/academic_assistant"
HOSTED = "postgresql+psycopg://u:p@ep-x.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"


def test_a_local_dev_url_keeps_its_server_with_the_test_database() -> None:
    assert _derive_test_database_url(None, LOCAL) == (
        "postgresql+psycopg://u:p@localhost:5432/shelf_test"
    )


def test_a_hosted_dev_url_is_not_followed() -> None:
    assert _derive_test_database_url(None, HOSTED) == COMPOSE_TEST_DATABASE_URL


def test_an_explicit_test_url_wins_even_when_hosted() -> None:
    assert _derive_test_database_url(HOSTED, LOCAL) == HOSTED
