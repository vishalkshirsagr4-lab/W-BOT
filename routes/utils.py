import logging

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/utils", tags=["Utility Tools 🛠️"])


@router.get("/weather")
async def get_weather(request: Request, city: str = Query(..., description="City name (e.g., Bengaluru, Tokyo)")):
    """Get the current weather for a city."""
    if not settings.WEATHER_API_KEY:
        return {
            "status": "warning",
            "message": "Weather API key is missing! Look outside the window for now! 🪟👀",
        }

    client = getattr(request.app.state, "http_client", None)
    if client is None:
        raise HTTPException(status_code=503, detail="HTTP client unavailable")

    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={settings.WEATHER_API_KEY}&units=metric"
        response = await client.get(url, timeout=3.0)

        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="City not found! Did you misspell it? 🗺️")

        data = response.json()
        temp = data["main"]["temp"]
        desc = data["weather"][0]["description"].title()

        return {
            "city": data["name"],
            "temperature": f"{temp}°C",
            "condition": desc,
            "message": f"It is currently {temp}°C with {desc} in {data['name']}! ✨",
        }
    except httpx.HTTPError:
        logger.exception("Weather lookup failed")
        raise HTTPException(status_code=503, detail="The weather satellite is down! 🛰️ Try again later.")


@router.get("/calc")
async def calculate_math(request: Request, expression: str = Query(..., description="Math expression (e.g., 2+2*5)")):
    """Calculate basic math expressions safely."""
    client = getattr(request.app.state, "http_client", None)
    if client is None:
        raise HTTPException(status_code=503, detail="HTTP client unavailable")

    try:
        url = f"http://api.mathjs.org/v4/?expr={expression}"
        response = await client.get(url, timeout=3.0)

        if response.status_code != 200:
            raise HTTPException(status_code=400, detail="Invalid math expression! My brain hurts! 🤕")

        return {
            "expression": expression,
            "result": response.text,
            "message": f"Easy peasy! The answer is {response.text} 🤓",
        }
    except httpx.HTTPError:
        logger.exception("Calculator request failed")
        raise HTTPException(status_code=500, detail="Calculator jammed! 📠")


@router.get("/qr")
async def generate_qr(data: str = Query(..., description="The URL or text to encode")):
    """Generate a QR code image URL for sharing links, notes, or contact info."""
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={data}"

    return {
        "status": "success",
        "qr_image_url": qr_url,
        "message": "Here is your QR code! Scan away! 📱✨",
    }


@router.get("/convert")
async def convert_units(
    value: float,
    from_unit: str = Query(..., description="km, mi, kg, lbs, c, f"),
    to_unit: str = Query(..., description="km, mi, kg, lbs, c, f"),
):
    """Basic unit conversion for physics homework."""
    from_unit = from_unit.lower()
    to_unit = to_unit.lower()

    result = None

    if from_unit == "c" and to_unit == "f":
        result = (value * 9 / 5) + 32
    elif from_unit == "f" and to_unit == "c":
        result = (value - 32) * 5 / 9
    elif from_unit == "km" and to_unit == "mi":
        result = value * 0.621371
    elif from_unit == "mi" and to_unit == "km":
        result = value * 1.60934
    elif from_unit == "kg" and to_unit == "lbs":
        result = value * 2.20462
    elif from_unit == "lbs" and to_unit == "kg":
        result = value / 2.20462

    if result is None:
        raise HTTPException(
            status_code=400,
            detail="I don't know how to convert those units yet! Stick to km/mi, kg/lbs, or c/f. 😅",
        )

    return {
        "original": f"{value} {from_unit}",
        "converted": f"{round(result, 2)} {to_unit}",
        "message": f"Done! {value}{from_unit} is equal to {round(result, 2)}{to_unit}. ⚡",
    }