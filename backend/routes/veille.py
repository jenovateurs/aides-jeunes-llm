"""Route de l'agent Veille : POST /api/veille/run (SSE)."""
import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.agents.veille import veille_agent

router = APIRouter()


class VeilleRunRequest(BaseModel):
    limit: int = 10
    only: list[str] = []
    model_name: str | None = None


def _sse(event_type: str, payload: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/veille/run")
async def run_veille(request: VeilleRunRequest):
    queue: asyncio.Queue = asyncio.Queue()
    DONE = object()

    async def emit(event_type: str, payload: dict):
        await queue.put((event_type, payload))

    async def driver():
        try:
            await veille_agent.run(request.model_dump(), emit=emit)
        finally:
            await queue.put(DONE)

    async def stream():
        task = asyncio.create_task(driver())
        try:
            while True:
                item = await queue.get()
                if item is DONE:
                    break
                event_type, payload = item
                yield _sse(event_type, payload)
        finally:
            await task

    return StreamingResponse(stream(), media_type="text/event-stream")
