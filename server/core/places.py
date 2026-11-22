"""Finding a place by its name (the wizard's Home step, the Zones editor) and
what follows from where someone lives (docs/zones.md, "Place search").

:func:`search` asks Open-Meteo's free geocoding service (no key; a plain HTTPS
call, ``GEOCODING_URL`` to point it elsewhere, empty to turn it off) for towns
and places matching a name, with their timezone and country. It never raises:
a failure, a timeout or the service turned off answers ``[]``, and the editors
fall back to typed coordinates and "Use my location".
:func:`currency_of` gives the currency of a country code.
"""

import logging
import os
from typing import Any, Dict, List

import aiohttp

logger = logging.getLogger(__name__)

DEFAULT_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
SEARCH_TIMEOUT_SECONDS = 6

# ISO 3166 country → ISO 4217 currency, for the countries people most often live in
# (the wizard suggests it; anyone can change it on the Profile card).
_EURO = ("AT", "BE", "CY", "DE", "EE", "ES", "FI", "FR", "GR", "HR", "IE", "IT", "LT", "LU", "LV", "MT", "NL",
         "PT", "SI", "SK")
CURRENCIES: Dict[str, str] = {
    **{c: "EUR" for c in _EURO},
    "US": "USD", "GB": "GBP", "IL": "ILS", "CA": "CAD", "AU": "AUD", "NZ": "NZD", "CH": "CHF", "SE": "SEK",
    "NO": "NOK", "DK": "DKK", "PL": "PLN", "CZ": "CZK", "HU": "HUF", "RO": "RON", "BG": "BGN", "UA": "UAH",
    "TR": "TRY", "RU": "RUB", "JP": "JPY", "CN": "CNY", "KR": "KRW", "IN": "INR", "SG": "SGD", "HK": "HKD",
    "TW": "TWD", "TH": "THB", "VN": "VND", "PH": "PHP", "ID": "IDR", "MY": "MYR", "AE": "AED", "SA": "SAR",
    "EG": "EGP", "ZA": "ZAR", "NG": "NGN", "KE": "KES", "MX": "MXN", "BR": "BRL", "AR": "ARS", "CL": "CLP",
    "CO": "COP", "PE": "PEN", "IS": "ISK", "JO": "JOD", "MA": "MAD",
}


def currency_of(country_code: str) -> str:
    """The currency of a country ("" when not known)."""
    return CURRENCIES.get((country_code or "").strip().upper(), "")


def _url() -> str:
    value = os.getenv("GEOCODING_URL")
    return DEFAULT_GEOCODING_URL if value is None else value.strip()


def _place(item: Dict[str, Any]) -> Dict[str, Any]:
    country = str(item.get("country_code") or "").upper()
    label = ", ".join(str(p) for p in (item.get("name"), item.get("admin1"), item.get("country")) if p)
    return {"name": str(item.get("name") or ""), "label": label, "latitude": float(item["latitude"]),
            "longitude": float(item["longitude"]), "timezone": str(item.get("timezone") or ""),
            "country_code": country, "currency": currency_of(country)}


async def search(query: str, *, language: str = "en", limit: int = 6) -> List[Dict[str, Any]]:
    """Places matching ``query``: ``[{name, label, latitude, longitude, timezone,
    country_code, currency}]`` ([] for a query under 2 characters, the service
    turned off, or any failure)."""
    query = (query or "").strip()
    url = _url()
    if len(query) < 2 or not url:
        return []
    params = {"name": query[:100], "count": str(max(1, min(limit, 10))), "language": language or "en",
              "format": "json"}
    try:
        timeout = aiohttp.ClientTimeout(total=SEARCH_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "dana-server"}) as session:
            async with session.get(url, params=params) as r:
                if r.status != 200:
                    logger.info(f"Place search answered {r.status}")
                    return []
                data = await r.json(content_type=None)
    except Exception as e:
        logger.info(f"Place search failed: {type(e).__name__}")
        return []
    out = []
    for item in (data or {}).get("results") or []:
        try:
            out.append(_place(item))
        except (KeyError, TypeError, ValueError):
            continue
    return out
