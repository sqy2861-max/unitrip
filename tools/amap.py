import json
import time

import httpx

from app.config import settings

BASE = "https://restapi.amap.com"
CACHE_FILE = "amap_cache.json"
MIN_INTERVAL = 0.4

_last_call = 0.0

try:
    with open(CACHE_FILE, encoding="utf-8") as f:
        _cache = json.load(f)
except Exception:
    _cache = {}


class AmapError(Exception):
    pass


def _save_cache():
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_cache, f, ensure_ascii=False)
    except OSError:
        pass


def _get(path: str, params: dict) -> dict:
    global _last_call

    cache_key = path + json.dumps(
        params, sort_keys=True, ensure_ascii=False
    )

    if cache_key in _cache:
        return _cache[cache_key]

    if not settings.amap_key:
        raise AmapError("未配置 AMAP_KEY，请检查 .env")

    wait = MIN_INTERVAL - (time.time() - _last_call)

    if wait > 0:
        time.sleep(wait)

    _last_call = time.time()

    resp = httpx.get(
        BASE + path,
        params={
            **params,
            "key": settings.amap_key
        },
        timeout=10,
    )

    resp.raise_for_status()
    data = resp.json()

    if data.get("status") != "1":
        raise AmapError(
            f"高德返回错误：{data.get('info')} "
            f"（{data.get('infocode')}）"
        )

    _cache[cache_key] = data
    _save_cache()

    return data


def geocode(address: str, city: str = "") -> dict:
    """地名转坐标。"""

    data = _get(
        "/v3/geocode/geo",
        {
            "address": address,
            "city": city
        },
    )

    items = data.get("geocodes") or []

    if not items:
        raise AmapError(f"找不到地点：{address}")

    loc = items[0]["location"]
    lng, lat = loc.split(",")

    return {
        "name": items[0].get("formatted_address", address),
        "location": loc,
        "lng": float(lng),
        "lat": float(lat),
        "adcode": items[0].get("adcode"),
    }


def _normalize_poi(p):
    """
    统一整理高德 POI 数据。
    保留地点类别编码，供后续严格分类使用。
    """

    biz = p.get("business") or {}

    return {
        "name": p.get("name"),
        "type": p.get("type"),
        "typecode": p.get("typecode"),
        "address": p.get("address"),
        "location": p.get("location"),

        "cityname": p.get("cityname"),
        "adname": p.get("adname"),

        "rating": biz.get("rating") or None,
        "cost": biz.get("cost") or None,
    }


def classify_poi(p):
    """
    根据高德 POI 分类编码判断地点类型。

    11 开头：旅游景点类别
    05 开头：餐饮服务类别

    其他类别不自动归入景点或餐馆。
    """

    code = str(p.get("typecode") or "")

    if code.startswith("11"):
        return "ATTRACTION"

    if code.startswith("05"):
        return "RESTAURANT"

    return "OTHER"


def search_places(
    keywords: str,
    city: str,
    limit: int = 10
) -> list:
    """在指定城市内按关键词搜索地点。"""

    data = _get(
        "/v5/place/text",
        {
            "keywords": keywords,
            "region": city,
            "city_limit": "true",
            "page_size": limit,
            "show_fields": "business",
        },
    )

    return [
        _normalize_poi(p)
        for p in data.get("pois") or []
    ]


def search_around(
    location: str,
    keywords: str,
    radius: int = 2500,
    limit: int = 20
) -> list:
    """以某个坐标为中心的周边搜索。"""

    data = _get(
        "/v5/place/around",
        {
            "location": location,
            "keywords": keywords,
            "radius": radius,
            "page_size": limit,
            "show_fields": "business",
        },
    )

    return [
        _normalize_poi(p)
        for p in data.get("pois") or []
    ]


def search_attractions(
    city: str,
    limit: int = 25
) -> list:
    """
    只返回高德分类编码属于旅游景点的 POI。
    """

    raw = search_places("景点", city, limit)

    return [
        p for p in raw
        if classify_poi(p) == "ATTRACTION"
    ]


def search_restaurants(
    city: str,
    limit: int = 25
) -> list:
    """
    只返回高德分类编码属于餐饮服务的 POI。
    """

    keywords_list = [
        "餐饮",
        "美食",
        "特色小吃"
    ]

    found = {}

    for keyword in keywords_list:

        raw = search_places(keyword, city, limit)

        for p in raw:

            if classify_poi(p) != "RESTAURANT":
                continue

            if not p.get("name") or not p.get("location"):
                continue

            found.setdefault(p["name"], p)

    return list(found.values())


def search_restaurants_around(
    location: str,
    radius: int = 2500,
    limit: int = 20
) -> list:
    """
    只返回指定坐标附近的餐饮服务 POI。
    """

    keywords_list = [
        "餐饮",
        "美食",
        "特色小吃"
    ]

    found = {}

    for keyword in keywords_list:

        raw = search_around(
            location,
            keyword,
            radius,
            limit
        )

        for p in raw:

            if classify_poi(p) != "RESTAURANT":
                continue

            if not p.get("name") or not p.get("location"):
                continue

            found.setdefault(p["name"], p)

    return list(found.values())


def route(
    origin: str,
    destination: str,
    mode: str = "walking"
) -> dict:
    """两点间路线。"""

    data = _get(
        f"/v5/direction/{mode}",
        {
            "origin": origin,
            "destination": destination,
            "show_fields": "cost",
        },
    )

    paths = data["route"]["paths"]

    if not paths:
        raise AmapError("没有查询到可用路线")

    path = paths[0]

    return {
        "distance_m": int(
            float(path.get("distance", 0))
        ),
        "duration_s": int(
            float(
                (path.get("cost") or {}).get(
                    "duration", 0
                )
            )
        ),
    }