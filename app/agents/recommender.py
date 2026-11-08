import json

from app.agents.extractor import client
from app.config import settings

SYSTEM_PROMPT = """你是 UniTrip，专为中国大学生设计的旅行规划助手。
根据用户的出发地、天数、人数、人均预算和偏好，推荐 3 个中国大陆的目的地。

硬性要求：
1. 四项费用（往返交通、住宿、餐饮、门票）的人均合计，必须小于或等于用户的人均预算。这是第一优先级。
2. 交通优先选高铁、普速火车、大巴，其次才是特价机票。往返单程不超过 5 小时，天数少时尽量选更近的。
3. 3 个目的地要有差异（如一个近郊小城、一个海滨或山水、一个人文古城），不要全是大城市。
4. 门票必须填合理的非零估算（景点、船票、缆车等），不能全部填 0。
5. 费用都是"每人"的估算，单位元，必须是整数。

大学生消费参考（用来估算）：
- 住宿：青旅或民宿多人间，每人每晚 60-120 元；住宿晚数 = 天数 - 1
- 餐饮：每人每天 60-100 元
- 高铁、大巴往返按实际距离估算，不要按机票价格估

只输出 JSON，不要输出其他文字，格式如下：
{
  "destinations": [
    {
      "name": "城市名",
      "reason": "一两句话说明为什么适合这次旅行，要提到预算和偏好",
      "transport": "从出发地怎么去（具体交通方式），大约多久",
      "highlights": ["特色景点或美食1", "特色景点或美食2", "特色景点或美食3"],
      "cost": {"transport": 0, "lodging": 0, "food": 0, "tickets": 0}
    }
  ]
}
cost 里四项分别是：往返交通、住宿、餐饮、门票，都是人均，不要自己计算合计。"""

MAX_TRIES = 3  # 最多生成几次
MIN_OK = 2     # 至少要有几个目的地在预算内


def to_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def call_llm(user_text: str) -> list:
    resp = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    data = json.loads(resp.choices[0].message.content)
    return data.get("destinations", [])[:3]


def process(raw_list: list, budget: int, people: int) -> list:
    result = []
    for d in raw_list:
        raw = d.get("cost") or {}
        parts = {k: to_int(raw.get(k)) for k in ("transport", "lodging", "food", "tickets")}
        total = sum(parts.values())
        d["cost"] = {**parts, "total": total}
        d["group_total"] = total * people
        d["within_budget"] = total <= budget
        d["budget_gap"] = total - budget
        result.append(d)
    return result


def recommend_destinations(needs: dict) -> dict:
    prefs = "、".join(needs.get("preferences") or []) or "无特别偏好"
    days = to_int(needs.get("days"))
    budget = to_int(needs.get("budget_per_person"))
    people = to_int(needs.get("people"))

    user_text = (
        f"出发地：{needs.get('origin')}\n"
        f"天数：{days}天{max(days - 1, 0)}晚\n"
        f"人数：{people}人\n"
        f"人均预算：{budget}元（四项费用合计不能超过这个数）\n"
        f"偏好：{prefs}"
    )

    best, best_ok, tries, hint = [], -1, 0, ""
    for _ in range(MAX_TRIES):
        tries += 1
        result = process(call_llm(user_text + hint), budget, people)
        ok = sum(1 for d in result if d["within_budget"])
        if ok > best_ok:
            best, best_ok = result, ok
        if ok >= MIN_OK:
            break
        over = "、".join(d.get("name", "") for d in result if not d["within_budget"])
        hint = (
            f"\n注意：上一次推荐的 {over} 合计超出了人均预算 {budget} 元。"
            f"请换成更便宜的目的地，或降低交通和住宿成本，"
            f"确保至少有两个目的地合计不超过 {budget} 元。"
        )

    # 预算内的排在前面，其次按总价从低到高
    best.sort(key=lambda x: (not x["within_budget"], x["cost"]["total"]))

    over_names = [d["name"] for d in best if not d["within_budget"]]
    note = "费用为模型估算，后续将接入实时数据校正，出行前请以实际价格为准。"
    if over_names:
        note = "注意：" + "、".join(over_names) + " 的估算超出了人均预算。" + note

    return {"destinations": best, "note": note, "attempts": tries}