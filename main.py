import os
from typing import List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量
load_dotenv()

app = FastAPI()

# ==========================================
# 1. 声明支持多轮对话的数据模型
# ==========================================
class Message(BaseModel):
    role: str      # 角色："user" 或 "assistant"
    content: str   # 消息内容

class ChatRequest(BaseModel):
    messages: List[Message]  # 接收前端传来的历史消息数组

# ==========================================
# 2. 全局配置与环境变量读取
# ==========================================
API_KEY = os.getenv("DASHSCOPE_API_KEY")
if not API_KEY:
    raise RuntimeError("🚨 环境变量 DASHSCOPE_API_KEY 未设置！请检查 .env 文件。")

BASE_URL = "https://ws-c56yietppdmtmf3m.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
MODEL_NAME = "qwen-plus"

SYSTEM_PROMPT = """
你是一位温和、内敛且极具共情力的 AI 心理觉察引导树洞。你基于认知行为疗法（CBT）和积极心理学框架，帮助用户通过文字记录实现自我洞察。
核心纪律：
1. 坚守“倾听与引导，而非提供现成答案或大道理”的原则。
2. 绝对不要像通用 AI 助手那样长篇大论，每次回复控制在 2-3 句话以内，保持克制与温度。
3. 每次回复的结尾，必须温和地提出一个且仅一个开放式问题，引导用户去观察自己身体的感受或当下的自动化思维。
"""

@app.post("/api/v1/chat")
async def handle_diary_chat(request: ChatRequest):
    # 动态组装 Payload：System Prompt 永远在第一位，紧接着拼接前端传来的历史对话记录
    formatted_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    # 遍历前端传来的消息，组装进上下文中
    for msg in request.messages:
        formatted_messages.append({"role": msg.role, "content": msg.content})

    payload = {
        "model": MODEL_NAME,
        "messages": formatted_messages,
        "temperature": 0.7
    }

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(
                f"{BASE_URL}/chat/completions",
                json=payload,
                headers=headers
            )
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code, 
                    detail=f"大模型 API 调用失败: {response.text}"
                )
                
            result = response.json()
            ai_response = result["choices"][0]["message"]["content"]
            
            return {
                "status": "success",
                "reply": ai_response
            }

        except httpx.RequestError as exc:
            raise HTTPException(status_code=500, detail=f"网络请求异常: {exc}")