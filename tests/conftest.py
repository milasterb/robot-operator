"""Tests run in English unless a test switches language itself, whatever
ROBOT_LANG says in the shell or in .env."""
import pytest

import i18n


@pytest.fixture(autouse=True)
def english():
    i18n.set_language("en")
    yield
    i18n.set_language("en")


@pytest.fixture
def czech():
    i18n.set_language("cs")
    yield
