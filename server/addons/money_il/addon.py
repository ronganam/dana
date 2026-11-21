"""The ``money_il`` addon: Israeli banks and cards for ``money`` (docs/money-il.md).

A provider of money's (docs/money.md, "Providers"): it registers its
companies (Bank Hapoalim, Cal, Psagot Trade, Pluxee), each read by the
bank-worker sidecar (``worker/``), the Bank of Israel's reference rates and
the price checks in Israel. Like money it has no credentials of its own:
connecting it is the on/off switch, each login is a money source. Turning it
off removes the user's logins at its companies (their accounts and
transactions stay in money). Its ``test`` asks whether the worker answers."""

from typing import Any, Optional

from core.addons import Addon, LlmJob, Worker
from core.users import User
from addons.money import providers, sources
from addons.money_il import companies, market, reference, worker_client
from addons.money_il.config import ADDON


async def _check(conn: Any) -> Optional[str]:
    """``POST /api/addons/money_il/test``: whether the bank worker answers."""
    return None if await worker_client.health() is not None else "bank worker not reachable"


def _changed(user: User) -> None:
    sources.provider_changed(user, ADDON)


addon = Addon(
    ADDON, order=141, needs=("money",), title="Money (Israel)",
    description="Israeli banks and cards, Psagot Trade and the Pluxee meal card for Money",
    llm_jobs={"money_market": LlmJob("balanced", "Money price checks")},
    # The bank-worker sidecar, built from worker/ by bin/deploy (docs/operations.md, "Workers").
    worker=Worker(port=8090, url_env="MONEY_WORKER_URL"),
    test=_check,
    on_change=_changed,
)

for _company in companies.COMPANIES:
    providers.register_company(_company)
providers.register_rates(providers.Rates(ADDON, reference.policy_rate, reference.fx_rates, reference.FX_CURRENCIES))
providers.register_price_checks(providers.PriceChecks(ADDON, market.TOPICS, market.latest, market.start))
