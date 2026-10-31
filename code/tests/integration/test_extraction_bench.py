"""The extraction bench (#74) on the 4 local sites, as a test (#75): real crawl, real model.

5 extractions per site; a field may be wrong in 1 run out of 5. The 2 real websites of
`scripts/bench_extract.py` stay out: their content changes without notice. Needs
OPENAI_API_KEY and Chromium.
"""

import bench_extract
import pytest
from openai import AsyncOpenAI

from builder.llm import OpenAIClient

pytestmark = [pytest.mark.browser, pytest.mark.llm]


@pytest.mark.parametrize("site", [s for s in bench_extract.SITES if s.local], ids=lambda s: s.name)
async def test_extraction_matches_what_the_site_states(site: bench_extract.Site) -> None:
    env = bench_extract.Env()
    assert env.openai_api_key is not None
    llm = OpenAIClient(
        AsyncOpenAI(api_key=env.openai_api_key.get_secret_value()),
        env.openai_model,
        reasoning_effort=env.builder_reasoning_effort,
    )

    assert await bench_extract.bench(site, llm, runs=5)
