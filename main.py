import os
from pathlib import Path
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

# ==========================================
# 防御性 Prompt 动态加载机制
# ==========================================
def load_system_prompt() -> str:
    """
    优先读取被 Git 忽略的生产环境私有提示词，
    若不存在（如开源仓库克隆者），则降级读取公开的 sample 提示词。
    """
    base_dir = Path(__file__).parent / "prompts"
    prod_path = base_dir / "system_prod.md"
    sample_path = base_dir / "system_sample.md"

    # 1. 尝试加载私密核心资产
    if prod_path.exists():
        with open(prod_path, "r", encoding="utf-8") as file:
            return file.read()
    
    # 2. 降级加载开源脱敏版本
    try:
        with open(sample_path, "r", encoding="utf-8") as file:
            print("⚠️ 警告: 未找到生产级 Prompt，正在使用开源 Sample 提示词启动。")
            return file.read()
    except FileNotFoundError:
        return "你是一个心理辅助树洞。" # 极限兜底

# 每次服务启动时加载
SYSTEM_PROMPT = load_system_prompt()

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