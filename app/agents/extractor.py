import json
from typing import Optional

from openai import OpenAI
from pydantic import BaseModel

from app.config import settings

client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)


class TripNeeds(BaseModel):
    origin: Optional[str] = None
    destination: Optional[str] = None
    days: Optional[int] = None
    people: Optional[int] = None
    budget_per_person: Optional[int] = None
    budget_total: Optional[int] = None
    preferences: list[str] = []


SYSTEM_PROMPT = """你是 UniTrip，一个专为大学生设计的旅行规划助手。
请从用户的话中提取旅行需求，只输出 JSON，不要输出其他文字。
字段：
- origin: 出发地（城市名）
- destination: 目的地，用户没说就填 null
- days: 旅行天数（整数），"3天2晚"记为 3；只说"五一""周末"等节日而没说几天，填 null
- people: 总人数（整数），"我和两个室友"记为 3，"我和女朋友"记为 2
- budget_per_person: 只有用户明确说"人均""每人"时才填，单位元（整数）
- budget_total: 只有用户说"一共""总共""合计""总预算"时才填，单位元（整数），不要自己除以人数
- preferences: 旅行偏好列表，如 ["美食", "拍照"]
没提到的字段一律填 null，不要猜测，不要做任何计算。"""

REQUIRED = ["origin", "days", "people", "budget_per_person"]

QUESTIONS = {
    "origin": "你们从哪个城市出发？",
    "days": "打算玩几天？",
    "people": "一共几个人同行？",
    "budget_per_person": "人均预算大概多少钱？",
}


def build_followup(missing: list[str]) -> str:
    if not missing:
        return "信息已经齐全，接下来可以为你推荐目的地了。"
    questions = "".join(QUESTIONS[f] for f in missing)
    return "为了帮你规划，还需要确认几个信息：" + questions


def call_llm(user_text: str) -> dict:
    resp = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    return json.loads(resp.choices[0].message.content)


def merge_needs(previous: dict, new: dict) -> dict:
    merged = dict(previous)
    for key, value in new.items():
        if value is None:
            continue
        if key == "preferences":
            old = merged.get("preferences") or []
            merged["preferences"] = old + [p for p in value if p not in old]
        else:
            merged[key] = value
    return merged


def extract_needs(user_text: str, previous: Optional[dict] = None) -> dict:
    new = call_llm(user_text)
    merged = merge_needs(previous or {}, new)
    clean = {k: v for k, v in merged.items() if v is not None}
    needs = TripNeeds(**clean)

    # 总预算由 Python 换算成人均，不让模型做除法
    if needs.budget_per_person is None and needs.budget_total and needs.people:
        needs.budget_per_person = round(needs.budget_total / needs.people)

    missing = [f for f in REQUIRED if getattr(needs, f) is None]
    return {
        "needs": needs.model_dump(),
        "missing": missing,
        "reply": build_followup(missing),
        "ready": not missing,
    }