import os
from pathlib import Path
from typing import List, Literal, cast
import uuid
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from dotenv import load_dotenv
import json
import re

from fastapi.middleware.cors import CORSMiddleware
from sqlmodel.ext.asyncio.session import AsyncSession
from database import get_session
from crud import (
    get_or_create_session, save_message, get_recent_messages, 
    get_user_by_email, create_user, merge_guest_into_user_session,
    try_acquire_advisory_lock, get_unprocessed_messages, create_diary,
    get_diaries_by_timeline, get_messages_by_diary
)
from auth import verify_password, create_access_token, SECRET_KEY, ALGORITHM
from schemas import UserCreate, UserLogin, Token, UserResponse
from models import User
import jwt

# 加载 .env 文件中的环境变量
load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 1. 声明支持多轮对话的数据模型
# ==========================================
class MessageModel(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str

class ChatRequest(BaseModel):
    session_id: str
    message: MessageModel

# ==========================================
# 2. 全局配置与环境变量读取
# ==========================================
API_KEY = os.getenv("DASHSCOPE_API_KEY")
if not API_KEY:
    raise RuntimeError("🚨 环境变量 DASHSCOPE_API_KEY 未设置！请检查 .env 文件。")

BASE_URL = "https://ws-c56yietppdmtmf3m.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
MODEL_NAME = "qwen3.7-max"

aclient = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)

# ==========================================
# 防御性 Prompt 动态加载机制
# ==========================================
def load_system_prompt() -> str:
    base_dir = Path(__file__).parent / "prompts"
    prod_path = base_dir / "system_prod.md"
    sample_path = base_dir / "system_sample.md"
    if prod_path.exists():
        with open(prod_path, "r", encoding="utf-8") as file:
            return file.read()
    try:
        with open(sample_path, "r", encoding="utf-8") as file:
            print("⚠️ 警告: 未找到生产级 Prompt，正在使用开源 Sample 提示词启动。")
            return file.read()
    except FileNotFoundError:
        return "你是一个心理辅助树洞。"

SYSTEM_PROMPT = load_system_prompt()

security = HTTPBearer(auto_error=False)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security), db: AsyncSession = Depends(get_session)) -> User | None:
    if not credentials:
        return None
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str | None = payload.get("sub")
        if email is None:
            return None
    except jwt.PyJWTError:
        return None
    user = await get_user_by_email(db, email=email)
    return user

@app.post("/api/v1/auth/register", response_model=Token)
async def register(user_in: UserCreate, db: AsyncSession = Depends(get_session)):
    user = await get_user_by_email(db, email=user_in.email)
    if user:
        raise HTTPException(status_code=400, detail="Email already registered")
    user = await create_user(db, email=user_in.email, password=user_in.password)
    
    merged_session_id = None
    if user_in.guest_session_id:
        merged_session_id = await merge_guest_into_user_session(db, user_in.guest_session_id, user.id)
        
    access_token = create_access_token(data={"sub": user.email})
    return {"access_token": access_token, "token_type": "bearer", "session_id": str(merged_session_id) if merged_session_id else None}

@app.post("/api/v1/auth/login", response_model=Token)
async def login(user_in: UserLogin, db: AsyncSession = Depends(get_session)):
    user = await get_user_by_email(db, email=user_in.email)
    if not user or not verify_password(user_in.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    merged_session_id = None
    if user_in.guest_session_id:
        merged_session_id = await merge_guest_into_user_session(db, user_in.guest_session_id, user.id)
        
    access_token = create_access_token(data={"sub": user.email})
    return {"access_token": access_token, "token_type": "bearer", "session_id": str(merged_session_id) if merged_session_id else None}

@app.get("/api/v1/users/me", response_model=UserResponse)
async def read_users_me(current_user: User = Depends(get_current_user)):
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return current_user

@app.get("/api/v1/chat/{session_id}/messages")
async def get_session_messages(session_id: str, db: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    try:
        session_id_uuid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session_id format.")
    
    session = await get_or_create_session(db, session_id_uuid)
    
    if session.user_id is not None:
        if current_user is None or session.user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access this session")
            
    recent_messages = await get_recent_messages(db, session_id_uuid, limit=100)
    return recent_messages

@app.post("/api/v1/chat")
async def handle_diary_chat(request: ChatRequest, db: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    try:
        session_id_uuid = uuid.UUID(request.session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session_id format. Must be a valid UUID.")

    # 1. 获取或创建会话
    session = await get_or_create_session(db, session_id_uuid)
    
    # Check session ownership
    if session.user_id is not None:
        if current_user is None or session.user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access this session")

    # 2. 落库最新的用户消息
    await save_message(db, session_id_uuid, request.message.role, request.message.content)

    # 3. 滑动窗口拉取历史并倒序（按时间正序）
    recent_messages = await get_recent_messages(db, session_id_uuid, limit=30)

    # 4. 组装 Payload：System Prompt 永远在第一位
    formatted_messages: List[ChatCompletionMessageParam] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in recent_messages:
        formatted_messages.append(cast(ChatCompletionMessageParam, {"role": msg.role, "content": msg.content}))

    async def generate():
        full_response_chunks = []
        try:
            stream = await aclient.chat.completions.create(
                model=MODEL_NAME,
                messages=formatted_messages,
                temperature=0.7,
                stream=True
            )
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_response_chunks.append(content)
                    yield f"data: {json.dumps(content, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            
            # AI 回复生成完毕后，将其落库
            full_response_text = "".join(full_response_chunks)
            if full_response_text:
                await save_message(db, session_id_uuid, "assistant", full_response_text)
                
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
            
            # 即使中间报错中断，只要有部分回复也尽力保存下来
            full_response_text = "".join(full_response_chunks)
            if full_response_text:
                try:
                    await save_message(db, session_id_uuid, "assistant", full_response_text + "\n[Error: Stream Interrupted]")
                except Exception as save_err:
                    print(f"Failed to save partial AI message: {save_err}")

    return StreamingResponse(generate(), media_type="text/event-stream")

@app.post("/api/v1/diary/generate")
async def generate_diary(session_id: str, db: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    try:
        session_id_uuid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session_id format.")

    # 1. Check session ownership
    session = await get_or_create_session(db, session_id_uuid)
    if session.user_id is not None:
        if current_user is None or session.user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access this session")

    # 2. Acquire Advisory Lock
    lock_id = hash(session_id) % (2**63 - 1)  # PosgreSQL expects a 64-bit integer
    acquired = await try_acquire_advisory_lock(db, lock_id)
    if not acquired:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Diary generation in progress")

    try:
        # 3. State pre-check: are there unprocessed messages?
        unprocessed_messages = await get_unprocessed_messages(db, session_id_uuid)
        if not unprocessed_messages:
            raise HTTPException(status_code=400, detail="No new messages to generate diary from.")

        # Limit token/messages window
        window_messages = unprocessed_messages[-50:]  # Limit to 50 latest unprocessed
        message_ids = [msg.id for msg in window_messages]

        # 4. Generate with LLM
        prompt = """
请分析以下用户的心理日记对话，提取出其“核心情绪”和一句“结构化洞察（Insight）”。
必须返回严格的 JSON 格式，包含 `core_emotion` (如"焦虑", "平静") 和 `insight` (一句话建议或反思)。
不要输出任何多余的废话、问候语或 Markdown 代码块标记，只输出 JSON！
示例：
{"core_emotion": "喜悦", "insight": "接纳不完美的自己，是内心平静的开始。"}
对话内容如下：
"""
        for msg in window_messages:
            prompt += f"\n{msg.role}: {msg.content}"

        response = await aclient.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": "你是一个专业的心理咨询师，善于总结情绪与洞察。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            response_format={"type": "json_object"}
        )

        response_text = response.choices[0].message.content or ""
        
        # 5. Regex extraction & Fallback parsing
        core_emotion = "情绪记录"
        insight = "（AI 提炼失败，请参考原始对话）"
        
        try:
            match = re.search(r'\{.*?\}', response_text, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                core_emotion = data.get("core_emotion", core_emotion)
                insight = data.get("insight", insight)
        except json.JSONDecodeError as e:
            print(f"JSON Parsing Error: {e}, Raw text: {response_text}")

        # 6. Create Diary
        diary = await create_diary(db, session_id_uuid, core_emotion, insight, message_ids)
        return diary
    finally:
        pass # The transaction commit/rollback handles releasing the advisory lock. Note: pg_try_advisory_xact_lock is released at transaction end automatically.

@app.get("/api/v1/diaries")
async def get_diaries(session_id: str, db: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    try:
        session_id_uuid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session_id format.")

    session = await get_or_create_session(db, session_id_uuid)
    if session.user_id is not None:
        if current_user is None or session.user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access this session")

    diaries = await get_diaries_by_timeline(db, session_id_uuid)
    return diaries

from models import Diary # make sure we import Diary if not already imported

@app.get("/api/v1/diaries/{diary_id}/messages")
async def get_diary_messages(diary_id: str, db: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)):
    try:
        diary_id_uuid = uuid.UUID(diary_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid format.")

    diary = await db.get(Diary, diary_id_uuid)
    if not diary:
        raise HTTPException(status_code=404, detail="Diary not found.")

    session = await get_or_create_session(db, diary.session_id)
    if session.user_id is not None:
        if current_user is None or session.user_id != current_user.id:
            # 返回 404 而不是 403，防止攻击者探测日记 ID 是否存在
            raise HTTPException(status_code=404, detail="Diary not found.")

    messages = await get_messages_by_diary(db, diary_id_uuid)
    return messages