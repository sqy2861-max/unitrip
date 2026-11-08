import math

from app.tools.amap import (
    AmapError,
    geocode,
    route,
    search_attractions,
    search_restaurants,
    search_around,
    classify_poi,
)


# =========================
# 基础参数
# =========================

WALK_LIMIT_M = 1200
MAX_RADIUS_KM = 25

STAY_MIN = {
    "attraction": 90,
    "breakfast": 35,
    "lunch": 60,
    "dinner": 75,
}

DEFAULT_MEAL = 35
TAXI_SEATS = 4

MID_SLOTS = {
    "relaxed": 3,
    "normal": 4,
    "packed": 5,
}

LOCAL_WORDS = ("小吃", "地方", "特色", "本地")

BAD_WORDS = (
    "麦当劳", "肯德基", "必胜客", "汉堡", "披萨",
    "西餐", "咖啡", "奶茶", "茶饮", "自助",
    "素食", "快餐", "冷饮", "甜品",
)


# =========================
# 基础数据处理
# =========================

def parse_loc(loc):
    lng, lat = loc.split(",")
    return float(lng), float(lat)


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def num(v, default):
    f = to_float(v)
    return round(f) if f is not None else default


def haversine_m(a, b):
    r = 6371000
    p1 = math.radians(a[1])
    p2 = math.radians(b[1])
    dp = p2 - p1
    dl = math.radians(b[0] - a[0])
    h = (
        math.sin(dp / 2) ** 2
        + math.cos(p1)
        * math.cos(p2)
        * math.sin(dl / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(h))


def clean_places(raw, center):
    seen = set()
    out = []
    for p in raw:
        name = p.get("name")
        loc = p.get("location")
        if not name or not loc or name in seen:
            continue
        try:
            pt = parse_loc(loc)
        except (ValueError, AttributeError):
            continue
        if haversine_m(pt, center) > MAX_RADIUS_KM * 1000:
            continue
        seen.add(name)
        out.append({
            **p,
            "pt": pt,
            "rating_num": to_float(p.get("rating")) or 0,
            "cost_num": to_float(p.get("cost")),
        })
    return out


# =========================
# 景点规划
# =========================

def daily_slots(days, pace="normal"):
    mid = MID_SLOTS.get(pace, 4)
    end = max(1, mid - 1)
    if days == 1:
        return [end]
    return [end] + [mid] * (days - 2) + [end]


def centroid(points):
    n = len(points)
    return (
        sum(p["pt"][0] for p in points) / n,
        sum(p["pt"][1] for p in points) / n,
    )


def nn_tour(points, center):
    left = list(points)
    cur = min(left, key=lambda p: haversine_m(p["pt"], center))
    left.remove(cur)
    tour = [cur]
    while left:
        cur = min(left, key=lambda p: haversine_m(p["pt"], cur["pt"]))
        left.remove(cur)
        tour.append(cur)
    return tour


# =========================
# 餐馆搜索与分类
# =========================

def meal_pool(group, city, center, meal_type, fallback):
    anchor = centroid(group)
    location = f"{anchor[0]:.6f},{anchor[1]:.6f}"
    if meal_type == "breakfast":
        keywords = ["早餐", "早餐店", "粥店", "肠粉"]
    elif meal_type == "lunch":
        keywords = ["午餐", "美食", "特色小吃", "餐饮"]
    else:
        keywords = ["晚餐", "美食", "特色小吃", "餐饮"]

    found = {}
    for keyword in keywords:
        try:
            raw = search_around(location, keyword, radius=2500, limit=20)
            for p in raw:
                if classify_poi(p) != "RESTAURANT":
                    continue
                if not p.get("name") or not p.get("location"):
                    continue
                found.setdefault(p["name"], p)
        except Exception:
            continue
        if len(found) >= 8:
            break

    if len(found) < 4:
        if "restaurants" not in fallback:
            fallback["restaurants"] = search_restaurants(city, limit=25)
        for p in fallback["restaurants"]:
            if classify_poi(p) != "RESTAURANT":
                continue
            if not p.get("name") or not p.get("location"):
                continue
            found.setdefault(p["name"], p)

    return clean_places(list(found.values()), center)


def food_score(food, near_pt, city_key, meal_type):
    name = food.get("name") or ""
    poi_type = food.get("type") or ""
    text = name + poi_type

    if any(word in text for word in BAD_WORDS):
        return -99

    score = food.get("rating_num") or 0

    if any(word in text for word in LOCAL_WORDS):
        score += 1.5

    if city_key and city_key in text:
        score += 1.0

    distance_km = haversine_m(food["pt"], near_pt) / 1000
    score -= distance_km * 0.5

    breakfast_words = ("早餐", "早点", "粥", "肠粉", "包子", "饺子", "馄饨", "面馆", "粉店", "豆浆")
    breakfast_avoid = ("烧烤", "火锅", "生腌", "鱼生", "烤肉", "夜宵", "酒吧")
    morning_only_words = ("早点", "早餐店", "手工早点", "早餐专门店")

    if meal_type == "breakfast":
        if any(word in text for word in breakfast_avoid):
            return -99
        if any(word in text for word in breakfast_words):
            score += 4
        else:
            score -= 2
    elif meal_type in ["lunch", "dinner"]:
        if any(word in name for word in morning_only_words):
            return -99

    return score


def pick_food(pool, used, near_pt, city_key, meal_type):
    candidates = [
        f for f in pool
        if f["name"] not in used and classify_poi(f) == "RESTAURANT"
    ]
    if not candidates:
        return None

    best = max(
        candidates,
        key=lambda f: food_score(f, near_pt, city_key, meal_type)
    )

    if food_score(best, near_pt, city_key, meal_type) < -50:
        return None

    used.add(best["name"])
    return best


# =========================
# 路线计算
# =========================

def make_leg(prev, cur):
    straight = haversine_m(prev["pt"], cur["pt"])
    walking = straight <= WALK_LIMIT_M
    mode = "walking" if walking else "driving"
    estimated = False
    try:
        result = route(prev["location"], cur["location"], mode)
        dist = result["distance_m"]
        secs = result["duration_s"]
    except Exception:
        estimated = True
        dist = int(straight * 1.3)
        secs = int(dist / 1.2) if walking else int(dist / 6) + 300
    fare = 0.0
    if not walking:
        km = dist / 1000
        fare = 10 if km <= 3 else 10 + 2.6 * (km - 3)

    return {
        "mode": "步行" if walking else "打车/公交",
        "distance_m": dist,
        "duration_min": max(1, round(secs / 60)),
        "taxi_fare": fare,
        "estimated": estimated,
    }


def fmt(minutes):
    minutes = int(round(minutes / 5) * 5)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


# =========================
# 每日行程生成
# =========================

def build_day(day_attrs, city, center, used, fallback, city_key, start_hour=8):
    half = max(1, len(day_attrs) // 2)
    morning = day_attrs[:half]
    afternoon = day_attrs[half:]

    breakfast_pool = meal_pool([morning[0]], city, center, "breakfast", fallback)
    lunch_pool = meal_pool([morning[-1]], city, center, "lunch", fallback)
    dinner_anchor = afternoon[-1] if afternoon else morning[-1]
    dinner_pool = meal_pool([dinner_anchor], city, center, "dinner", fallback)

    breakfast = pick_food(breakfast_pool, used, morning[0]["pt"], city_key, "breakfast")
    lunch = pick_food(lunch_pool, used, morning[-1]["pt"], city_key, "lunch")
    dinner = pick_food(dinner_pool, used, dinner_anchor["pt"], city_key, "dinner")

    attractions = []
    meals = {"breakfast": None, "lunch": None, "dinner": None}
    stops = []
    sequence = []

    if breakfast:
        sequence.append(("breakfast", breakfast))
    sequence += [("attraction", p) for p in morning]
    if lunch:
        sequence.append(("lunch", lunch))
    sequence += [("attraction", p) for p in afternoon]
    if dinner:
        sequence.append(("dinner", dinner))

    clock = start_hour * 60
    previous = None
    travel_cost = 0.0

    for kind, p in sequence:
        leg = None
        if previous is not None:
            leg = make_leg(previous, p)
            clock += leg["duration_min"]
            travel_cost += leg["taxi_fare"]

        if kind == "breakfast":
            clock = max(clock, 8 * 60)
        elif kind == "lunch":
            clock = max(clock, 12 * 60)
        elif kind == "dinner":
            clock = max(clock, 17 * 60 + 30)

        item = {
            "type": kind,
            "name": p["name"],
            "address": p.get("address"),
            "location": p["location"],
            "typecode": p.get("typecode"),
            "poi_type": p.get("type"),
            "rating": p.get("rating"),
            "avg_cost": p.get("cost_num"),
            "time": fmt(clock),
            "travel": leg,
        }

        if kind == "attraction":
            attractions.append(item)
        else:
            item["meal_type"] = kind
            meals[kind] = item

        stops.append(item)
        clock += STAY_MIN[kind]
        previous = p

    warnings = []
    for meal_type, meal in meals.items():
        if meal is None:
            labels = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}
            warnings.append(f"未找到可靠的{labels[meal_type]}餐馆")

    return {
        "attractions": attractions,
        "meals": meals,
        "stops": stops,
        "warnings": warnings,
        "local_transport_cost": travel_cost,
    }


# =========================
# 预算计算
# =========================

def build_budget(dest_cost, days, people, budget, all_meals, taxi_total):
    cars = math.ceil(people / TAXI_SEATS)
    local = round(taxi_total * cars / people)

    meal_sum = 0
    for meal in all_meals:
        cost = meal.get("cost_num")
        meal_sum += DEFAULT_MEAL if cost is None else round(cost)

    has = lambda k: to_float(dest_cost.get(k)) is not None
    ref = "参考估算（来自目的地推荐）"
    dft = "默认估算"

    items = [
        {"name": "往返交通", "amount": num(dest_cost.get("transport"), 0), "source": ref if has("transport") else "未提供"},
        {"name": "住宿", "amount": num(dest_cost.get("lodging"), 90 * max(days - 1, 0)), "source": ref if has("lodging") else dft},
        {"name": "餐饮", "amount": meal_sum, "source": "餐馆人均消费；缺失时按每餐35元估算"},
        {"name": "门票", "amount": num(dest_cost.get("tickets"), 60), "source": ref if has("tickets") else dft},
        {"name": "市内交通", "amount": local, "source": "高德路线距离及交通费用估算"},
    ]

    total = sum(item["amount"] for item in items)

    return {
        "items": items,
        "total_per_person": total,
        "budget_per_person": budget,
        "gap": total - budget,
        "within_budget": total <= budget,
        "group_total": total * people,
    }


# =========================
# 总规划入口
# =========================

def plan_trip(needs, destination):
    city = destination["name"]
    days = max(int(needs.get("days") or 1), 1)
    people = max(int(needs.get("people") or 1), 1)
    budget = int(needs.get("budget_per_person") or 0)
    pace = needs.get("pace") or "normal"
    city_key = city.replace("市", "")

    info = geocode(city)
    center = (info["lng"], info["lat"])

    raw_attrs = search_attractions(city, limit=25)
    attrs = clean_places(raw_attrs, center)

    if not attrs:
        raise AmapError(f"在{city}没有搜到符合分类条件的景点")

    slots = daily_slots(days, pace)
    required = sum(slots)
    chosen = sorted(attrs, key=lambda p: -p["rating_num"])[:required]

    if not chosen:
        raise AmapError(f"{city}没有可用于规划的景点")

    tour = nn_tour(chosen, center)

    days_out = []
    used_restaurants = set()
    all_meals = []
    taxi_total = 0.0
    idx = 0
    fallback = {}

    for d, n in enumerate(slots):
        group = tour[idx:idx + n]
        idx += n
        if not group:
            continue

        day_result = build_day(
            day_attrs=group,
            city=city,
            center=center,
            used=used_restaurants,
            fallback=fallback,
            city_key=city_key,
            start_hour=8,
        )

        all_meals.extend([meal for meal in day_result["meals"].values() if meal is not None])
        taxi_total += day_result["local_transport_cost"]

        days_out.append({
            "day": d + 1,
            "attractions": day_result["attractions"],
            "meals": day_result["meals"],
            "stops": day_result["stops"],
            "warnings": day_result["warnings"],
        })

    budget_out = build_budget(
        destination.get("cost") or {},
        days,
        people,
        budget,
        all_meals,
        taxi_total,
    )

    return {
        "destination": city,
        "pace": pace,
        "center": info["location"],
        "days": days_out,
        "budget": budget_out,
        "note": (
            "景点和餐馆分别依据高德 POI 分类筛选。"
            "餐馆名称和地址来自地图搜索结果；"
            "人均消费缺失时使用估算值。"
            "营业时间和招牌菜需进一步核实。"
        ),
    }