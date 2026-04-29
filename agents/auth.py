"""auth.py - Shared Anthropic client factory with consistent auth bootstrap.

All agent modules should use make_client() instead of duplicating the
load_dotenv / ANTHROPIC_AUTH_TOKEN / Anthropic() bootstrap block.
"""

from __future__ import annotations

import os

from anthropic import Anthropic
from dotenv import load_dotenv


def make_client() -> tuple[Anthropic, str]:
    """Load env, resolve credentials, and return (client, model_id).

    Behaviour:
    - Calls load_dotenv(override=True) so a .env file always wins over
      stale shell exports.
    - When ANTHROPIC_BASE_URL is set (e.g. a local proxy / test harness),
      strips ANTHROPIC_AUTH_TOKEN from the environment so the SDK uses
      api_key as the sole credential source and avoids auth conflicts.
    - Returns the instantiated Anthropic client and the MODEL_ID string so
      callers can unpack them in one line:

          client, MODEL = make_client()
    """
    load_dotenv(override=True)

    # A custom base_url means we are talking to a proxy or test double.
    # The Anthropic SDK can inadvertently pick up ANTHROPIC_AUTH_TOKEN and
    # use it instead of api_key, causing 401s against non-Anthropic hosts.
    if os.getenv("ANTHROPIC_BASE_URL"):
        os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

    client = Anthropic(
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        base_url=os.getenv("ANTHROPIC_BASE_URL"),
    )
    model = os.environ["MODEL_ID"]
    return client, model
