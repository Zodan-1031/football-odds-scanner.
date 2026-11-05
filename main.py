import os
from datetime import date
from typing import Any

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

API_URL = "https://v3.football.api-sports.io"
API_KEY = os.getenv("API_FOOTBALL_KEY", "").strip()

app = FastAPI(title="Football Odds Scanner")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


def api_get(path: str, params: dict[str, Any]):
    if not API_KEY:
        raise HTTPException(
            status_code=503,
            detail="API key is not configured. Add API_FOOTBALL_KEY in Render Environment."
        )
    try:
        response = requests.get(
            f"{API_URL}/{path}",
            headers={"x-apisports-key": API_KEY},
            params=params,
            timeout=25,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException:
        raise HTTPException(status_code=502, detail="Could not reach API-Football.")
    except ValueError:
        raise HTTPException(status_code=502, detail="API-Football returned invalid JSON.")

    errors = payload.get("errors")
    if errors:
        raise HTTPException(status_code=502, detail={"api_errors": errors})
    return payload.get("response", [])


def normalize_label(label: str):
    s = label.lower().strip()
    if s in {"home", "1", "team 1"} or "home" in s:
        return "home"
    if s in {"draw", "x", "tie"} or "draw" in s:
        return "draw"
    if s in {"away", "2", "team 2"} or "away" in s:
        return "away"
    return None


def extract_1x2(item: dict):
    fixture = item.get("fixture") or {}
    teams = item.get("teams") or {}
    home_name = (teams.get("home") or {}).get("name") or "Home"
    away_name = (teams.get("away") or {}).get("name") or "Away"
    result = {
        "fixture_id": fixture.get("id"),
        "date": fixture.get("date"),
        "home": home_name,
        "away": away_name,
        "books": [],
    }

    for bookmaker in item.get("bookmakers", []):
        outcomes = {}
        market_name = None
        for bet in bookmaker.get("bets", []):
            bet_name = (bet.get("name") or "").lower()
            if any(term in bet_name for term in ("match winner", "1x2", "fulltime result", "full time result")):
                vals = {}
                for value in bet.get("values", []):
                    side = normalize_label(str(value.get("value", "")))
                    try:
                        odd = float(value.get("odd"))
                    except (TypeError, ValueError):
                        continue
                    if side and odd > 1:
                        vals[side] = odd
                if len(vals) == 3:
                    outcomes = vals
                    market_name = bet.get("name")
                    break
        if outcomes:
            result["books"].append({
                "name": bookmaker.get("name", "Bookmaker"),
                "market": market_name,
                "odds": outcomes,
            })
    return result


@app.get("/api/odds")
def odds(date_: str = Query(default_factory=lambda: date.today().isoformat(), alias="date")):
    try:
        date.fromisoformat(date_)
    except ValueError:
        raise HTTPException(status_code=400, detail="Date must be YYYY-MM-DD.")
    raw = api_get("odds", {"date": date_, "page": 1})
    matches = []
    for item in raw:
        match = extract_1x2(item)
        if match["books"]:
            matches.append(match)

    # Cross-book best prices and theoretical arbitrage check for each match.
    for match in matches:
        sides = ("home", "draw", "away")
        best = {}
        for side in sides:
            candidates = [
                (book["odds"][side], book["name"])
                for book in match["books"] if side in book["odds"]
            ]
            if candidates:
                best[side] = {
                    "odd": max(candidates, key=lambda x: x[0])[0],
                    "bookmaker": max(candidates, key=lambda x: x[0])[1],
                }
        match["best"] = best
        if all(side in best for side in sides):
            inverse_sum = sum(1 / best[side]["odd"] for side in sides)
            match["arb_percent"] = round((1 - inverse_sum) * 100, 2)
            match["arbitrage"] = inverse_sum < 1
        else:
            match["arb_percent"] = None
            match["arbitrage"] = False

    return {
        "date": date_,
        "count": len(matches),
        "matches": matches,
        "notice": "Odds availability depends on league, bookmaker, subscription and API coverage."
    }


@app.get("/health")
def health():
    return {"ok": True, "api_key_configured": bool(API_KEY)}
