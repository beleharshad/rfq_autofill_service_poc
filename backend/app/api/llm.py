from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import llm_service

router = APIRouter()


class LLMRequest(BaseModel):
    prompt: str
    temperature: float = 0.2
    max_output_tokens: int = 512


@router.post("/generate")
async def generate(req: LLMRequest):
    try:
        text = llm_service.generate_text(req.prompt, req.temperature, req.max_output_tokens)
        return {"text": text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
