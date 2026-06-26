import pytest


@pytest.fixture
def anyio_backend():
    # Run anyio-marked ASGI tests on asyncio only (no trio dependency).
    return "asyncio"
