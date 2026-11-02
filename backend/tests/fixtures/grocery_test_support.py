"""Offline integration setup using the extracted production helper contract."""
from contextlib import ExitStack, contextmanager
import hashlib
from unittest import mock
from urllib.parse import urlparse
import urllib.request

import grocery_helper_adapter
from fixtures import grocery_helper_contract
from test_grocery_helper_adapter import FakeNotion


@contextmanager
def grocery_environment(directory):
    source = grocery_helper_contract.source()
    helper = directory / "synthetic_helper.py"
    helper.write_text(source)
    fake = FakeNotion()
    original_open = urllib.request.urlopen
    def transport(req, timeout):
        if urlparse(req.full_url).hostname == "127.0.0.1":
            return original_open(req, timeout=timeout)
        return fake.open(req, timeout)
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(grocery_helper_adapter, "PINNED_HELPER_SHA256", hashlib.sha256(source.encode()).hexdigest()))
        stack.enter_context(mock.patch.dict("os.environ", {"AURA_CAPTURE_HOME": str(directory / "fake-home"), "NOTION_API_TOKEN": "synthetic-token"}, clear=True))
        stack.enter_context(mock.patch("urllib.request.urlopen", side_effect=transport))
        yield helper, fake
