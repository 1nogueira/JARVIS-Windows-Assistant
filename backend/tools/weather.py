from __future__ import annotations

import re
import unicodedata
from typing import Any

import httpx

from backend.core.config import SettingsStore
from backend.security.permissions import PermissionLevel
from backend.tools.registry import ToolRegistry
from backend.tools.results import explicit_success


WEATHER_CODES = {
    0: "céu limpo",
    1: "predominantemente limpo",
    2: "parcialmente nublado",
    3: "nublado",
    45: "neblina",
    48: "neblina com geada",
    51: "garoa fraca",
    53: "garoa moderada",
    55: "garoa forte",
    61: "chuva fraca",
    63: "chuva moderada",
    65: "chuva forte",
    80: "pancadas de chuva fracas",
    81: "pancadas de chuva moderadas",
    82: "pancadas de chuva fortes",
    95: "trovoadas",
    96: "trovoadas com granizo",
    99: "trovoadas fortes com granizo",
}


def canonicalize_location(location: str, default_location: str = "") -> str:
    place = re.sub(r"\s+", " ", location).strip(" ,.?!")
    default = re.sub(r"\s+", " ", default_location).strip(" ,.?!")
    if not default:
        return place

    def normalized(value: str) -> str:
        plain = unicodedata.normalize("NFKD", value.casefold()).encode("ascii", "ignore").decode()
        return re.sub(r"[^a-z0-9]", "", plain)

    candidate_key = normalized(place)
    # Preserve regional context only when the requested city matches the configured city.
    default_keys = {normalized(default), normalized(default.split(",", 1)[0])}
    if candidate_key and candidate_key in default_keys:
        return default
    return place


async def current_weather(
    location: str, day_offset: int = 0, default_location: str = ""
) -> dict[str, Any]:
    place = canonicalize_location(location, default_location)
    if len(place) < 2:
        raise ValueError("Informe uma cidade para consultar o clima.")
    day_offset = max(0, min(int(day_offset), 7))
    timeout = httpx.Timeout(12)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        geocode = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": place, "count": 1, "language": "pt", "format": "json"},
        )
        geocode.raise_for_status()
        matches = geocode.json().get("results") or []
        if not matches:
            raise ValueError(f"Não encontrei a localidade: {place}.")
        match = matches[0]
        params: dict[str, Any] = {
            "latitude": match["latitude"],
            "longitude": match["longitude"],
            "timezone": "auto",
        }
        if day_offset:
            params.update(
                {
                    "daily": (
                        "weather_code,temperature_2m_max,temperature_2m_min,"
                        "apparent_temperature_max,apparent_temperature_min,"
                        "precipitation_probability_max,wind_speed_10m_max"
                    ),
                    "forecast_days": day_offset + 1,
                }
            )
        else:
            params["current"] = (
                "temperature_2m,apparent_temperature,relative_humidity_2m,"
                "weather_code,wind_speed_10m,is_day"
            )
        forecast = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
        forecast.raise_for_status()
    payload = forecast.json()
    base = {
        "location": match.get("name", place),
        "state": match.get("admin1", ""),
        "country": match.get("country", ""),
        "source": "Open-Meteo",
        "source_url": "https://open-meteo.com/",
    }
    if day_offset:
        daily = payload.get("daily") or {}

        def at(name: str) -> Any:
            values = daily.get(name) or []
            return values[day_offset] if len(values) > day_offset else None

        code = int(at("weather_code") if at("weather_code") is not None else -1)
        return {
            **base,
            "day_offset": day_offset,
            "forecast_date": at("time"),
            "temperature_max_c": at("temperature_2m_max"),
            "temperature_min_c": at("temperature_2m_min"),
            "feels_like_max_c": at("apparent_temperature_max"),
            "feels_like_min_c": at("apparent_temperature_min"),
            "precipitation_probability_percent": at("precipitation_probability_max"),
            "wind_max_kmh": at("wind_speed_10m_max"),
            "condition": WEATHER_CODES.get(code, "condição meteorológica variável"),
        }
    current = payload.get("current") or {}
    code = int(current.get("weather_code", -1))
    return {
        **base,
        "day_offset": 0,
        "temperature_c": current.get("temperature_2m"),
        "feels_like_c": current.get("apparent_temperature"),
        "humidity_percent": current.get("relative_humidity_2m"),
        "wind_kmh": current.get("wind_speed_10m"),
        "condition": WEATHER_CODES.get(code, "condição meteorológica variável"),
        "observed_at": current.get("time"),
    }


def register_weather_tools(registry: ToolRegistry, settings: SettingsStore) -> None:
    @registry.tool(
        name="get_current_weather",
        description=(
            "Consulta temperatura, sensação térmica, umidade, vento e condição atual de uma "
            "cidade. Use sempre que o usuário perguntar clima, tempo, temperatura ou quantos graus."
        ),
        parameters={
            "type": "object",
            "properties": {
                "location": {"type": "string"},
                "day_offset": {"type": "integer", "minimum": 0, "maximum": 7},
            },
            "required": ["location"],
        },
        permission_level=PermissionLevel.SAFE,
        category="Internet",
    )
    async def get_current_weather(location: str, day_offset: int = 0) -> dict[str, Any]:
        try:
            default_location = str(settings.section("user").get("default_location", ""))
            return explicit_success(
                await current_weather(location, day_offset, default_location)
            )
        except httpx.HTTPError as exc:
            raise RuntimeError("O serviço gratuito de clima está temporariamente indisponível.") from exc
