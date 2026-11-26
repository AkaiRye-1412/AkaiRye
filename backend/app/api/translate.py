"""英中翻译（首页翻译卡片）：POST /api/translate。

契约：
- 成功：{translation, explanation, direction, model}；
- 参数问题 → 400 invalid_request；LLM 未配置 → 400 llm_not_configured；
- 云端调用失败/超时 → 502 model_error（对齐 api-rules 第 5 节）。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlite3 import Connection

from app.api.deps import get_db
from app.services import translate_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/translate", tags=["translate"])


class TranslateBody(BaseModel):
    text: str
    direction: str = "auto"  # auto | en-zh | zh-en


@router.post("")
def translate(body: TranslateBody, conn: Connection = Depends(get_db)) -> dict:
    try:
        return translate_service.translate(conn, body.text, body.direction)
    except translate_service.LlmNotConfigured as exc:
        raise HTTPException(
            status_code=400, detail={"code": "llm_not_configured", "message": str(exc)}
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "invalid_request", "message": str(exc)}
        ) from exc
    except Exception as exc:  # noqa: BLE001 —— 云端鉴权/限频/超时统一按模型错误返回
        logger.exception("翻译失败")
        raise HTTPException(
            status_code=502,
            detail={"code": "model_error", "message": f"翻译失败：{str(exc)[:160]}"},
        ) from exc
