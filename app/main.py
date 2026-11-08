from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.responses import RedirectResponse # 【修改点 1：新增这行导入】

from app.agents.extractor import extract_needs
from app.agents.recommender import recommend_destinations
from app.services.planner import plan_trip
from app.tools.amap import AmapError, geocode, search_places

app = FastAPI(title="UniTrip", version="0.1.0")


class TripRequest(BaseModel):
    origin: str
    people: int
    days: int
    budget_per_person: int
    preferences: list[str] = []


class ChatRequest(BaseModel):
    message: str
    previous: Optional[dict] = None


class RecommendRequest(BaseModel):
    needs: dict


class PlanRequest(BaseModel):
    needs: dict
    destination: dict


@app.get("/")
def root():
    # 【修改点 2：把原本返回 JSON 的代码，改成自动跳转到网页】
    return RedirectResponse(url="/ui")


@app.post("/parse")
def parse_request(req: TripRequest):
    return {
        "status": "ok",
        "received": req.model_dump(),
        "missing": ["destination"],
    }


@app.post("/chat")
def chat(req: ChatRequest):
    return extract_needs(req.message, req.previous)


@app.post("/recommend")
def recommend(req: RecommendRequest):
    return recommend_destinations(req.needs)


@app.post("/plan")
def plan(req: PlanRequest):
    if not req.destination.get("name"):
        raise HTTPException(status_code=422, detail="destination 需要包含 name")
    try:
        return plan_trip(req.needs, req.destination)
    except AmapError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/amap/test")
def amap_test(city: str = "潮州"):
    try:
        return {
            "city": geocode(city),
            "attractions": search_places("景点", city, 5),
            "food": search_places("美食", city, 5),
        }
    except AmapError as e:
        raise HTTPException(status_code=502, detail=str(e))

        
# 挂载静态资源目录
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/ui")
def read_index():
    return FileResponse("static/index.html")