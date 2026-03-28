"""Currency exchange rate service.

Fetches live exchange rates from free APIs.
Falls back to provided rates if API is unavailable.
"""

from __future__ import annotations

import time
from typing import Dict, Optional, Tuple
import requests

# Cache exchange rates to avoid excessive API calls
_rate_cache: Dict[str, Tuple[float, float]] = {}  # {currency_pair: (rate, timestamp)}
CACHE_TTL_SECONDS = 3600  # Cache rates for 1 hour


def get_live_exchange_rate(
    from_currency: str = "USD",
    to_currency: str = "INR",
    fallback_rate: Optional[float] = None,
    include_timestamp: bool = False,
) -> Tuple[float, str] | Tuple[float, str, str]:
    """
    Get live exchange rate between two currencies.
    
    Args:
        from_currency: Source currency code (e.g., "USD")
        to_currency: Target currency code (e.g., "INR")
        fallback_rate: Rate to use if API fails
        include_timestamp: If True, returns (rate, source, timestamp_str)
        
    Returns:
        Tuple of (exchange_rate, source) or (exchange_rate, source, timestamp) if include_timestamp=True
    """
    from datetime import datetime
    
    cache_key = f"{from_currency.upper()}_{to_currency.upper()}"
    
    # Check cache first
    if cache_key in _rate_cache:
        cached_rate, cached_time = _rate_cache[cache_key]
        if time.time() - cached_time < CACHE_TTL_SECONDS:
            if include_timestamp:
                ts = datetime.fromtimestamp(cached_time).strftime("%d-%b-%Y %H:%M:%S")
                return cached_rate, "cached", ts
            return cached_rate, "cached"
    
    # Try multiple free APIs
    fetch_time = time.time()
    rate = _try_frankfurter_api(from_currency, to_currency)
    if rate is None:
        rate = _try_exchangerate_api(from_currency, to_currency)
    
    if rate is not None:
        # Cache the rate
        _rate_cache[cache_key] = (rate, fetch_time)
        if include_timestamp:
            ts = datetime.fromtimestamp(fetch_time).strftime("%d-%b-%Y %H:%M:%S")
            return rate, "live", ts
        return rate, "live"
    
    # Fallback
    if fallback_rate is not None:
        if include_timestamp:
            return fallback_rate, "fallback", "N/A (fallback rate)"
        return fallback_rate, "fallback"
    
    # Default fallback rates for common pairs
    default_rates = {
        "USD_INR": 83.5,
        "EUR_INR": 90.0,
        "GBP_INR": 105.0,
        "INR_USD": 0.012,
    }
    rate = default_rates.get(cache_key, 1.0)
    if include_timestamp:
        return rate, "default", "N/A (default rate)"
    return rate, "default"


def _try_frankfurter_api(from_currency: str, to_currency: str) -> Optional[float]:
    """
    Fetch rate from Frankfurter API (free, no API key needed).
    https://www.frankfurter.app/
    """
    try:
        url = f"https://api.frankfurter.app/latest?from={from_currency.upper()}&to={to_currency.upper()}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            rates = data.get("rates", {})
            return rates.get(to_currency.upper())
    except Exception as e:
        print(f"[CurrencyService] Frankfurter API error: {e}")
    return None


def _try_exchangerate_api(from_currency: str, to_currency: str) -> Optional[float]:
    """
    Fetch rate from ExchangeRate-API (free tier).
    https://open.er-api.com/
    """
    try:
        url = f"https://open.er-api.com/v6/latest/{from_currency.upper()}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("result") == "success":
                rates = data.get("rates", {})
                return rates.get(to_currency.upper())
    except Exception as e:
        print(f"[CurrencyService] ExchangeRate-API error: {e}")
    return None


def get_all_rates_for_currency(base_currency: str = "USD") -> Dict[str, float]:
    """
    Get all exchange rates for a base currency.
    
    Returns:
        Dict mapping currency codes to rates
    """
    try:
        url = f"https://open.er-api.com/v6/latest/{base_currency.upper()}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("result") == "success":
                return data.get("rates", {})
    except Exception as e:
        print(f"[CurrencyService] Error fetching all rates: {e}")
    
    return {}


def clear_cache() -> None:
    """Clear the rate cache."""
    global _rate_cache
    _rate_cache = {}
