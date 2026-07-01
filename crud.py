from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, col
from sqlalchemy import text
import uuid
from models import ChatSession, Message, User, Diary
from auth import get_password_hash

async def get_or_create_session(db: AsyncSession, session_id: uuid.UUID) -> ChatSession:
    session = await db.get(ChatSession, session_id)
    if not session:
        session = ChatSession(id=session_id)
        db.add(session)
        await db.commit()
        await db.refresh(session)
    return session

async def save_message(db: AsyncSession, session_id: uuid.UUID, role: str, content: str) -> Message:
    msg = Message(session_id=session_id, role=role, content=content)
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return msg

async def get_recent_messages(db: AsyncSession, session_id: uuid.UUID, limit: int = 30) -> list[Message]:
    statement = select(Message).where(col(Message.session_id) == session_id).order_by(col(Message.created_at).desc()).limit(limit)
    result = await db.exec(statement)
    messages = result.all()
    # Reverse to get chronological order
    return list(reversed(messages))

async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    statement = select(User).where(col(User.email) == email)
    result = await db.exec(statement)
    return result.first()

async def create_user(db: AsyncSession, email: str, password: str) -> User:
    hashed_password = get_password_hash(password)
    user = User(email=email, hashed_password=hashed_password)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user

async def merge_guest_into_user_session(db: AsyncSession, guest_session_id: uuid.UUID, user_id: uuid.UUID) -> uuid.UUID:
    # 1. 查找用户的主会话 (最早创建的会话)
    statement = select(ChatSession).where(col(ChatSession.user_id) == user_id).order_by(col(ChatSession.created_at).asc())
    result = await db.exec(statement)
    main_session = result.first()

    if not main_session:
        # 如果用户没有历史会话，直接将这个 guest session 设为主会话
        session = await db.get(ChatSession, guest_session_id)
        if session:
            session.user_id = user_id
            db.add(session)
        else:
            session = ChatSession(id=guest_session_id, user_id=user_id)
            db.add(session)
        await db.commit()
        return guest_session_id

    # 2. 如果用户已有主会话，且 guest session 与主会话不同，执行数据合并
    if main_session.id != guest_session_id:
        from sqlalchemy import update
        
        # 将所有未绑定或绑定在 guest_session_id 上的 Message 和 Diary 更新为 main_session.id
        # 使用 update 语句确保原子性，并减少内存消耗
        stmt_messages = (
            update(Message)
            .where(col(Message.session_id) == guest_session_id)
            .values(session_id=main_session.id)
        )
        
        stmt_diaries = (
            update(Diary)
            .where(col(Diary.session_id) == guest_session_id)
            .values(session_id=main_session.id)
        )
        
        try:
            await db.exec(stmt_messages)  # 使用 exec() 替代已弃用/有类型警告的 execute()
            await db.exec(stmt_diaries)
            # 可选：删除旧的空 guest session (这里先保留以避免外键冲突或因为没有 cascade 导致的错误，数据已被抽干)
            await db.commit()
        except Exception as e:
            await db.rollback()
            raise e
            
    return main_session.id
async def try_acquire_advisory_lock(db: AsyncSession, lock_id: int) -> bool:
    result = await db.execute(text("SELECT pg_try_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})
    return result.scalar() is True

async def get_unprocessed_messages(db: AsyncSession, session_id: uuid.UUID) -> list[Message]:
    statement = select(Message).where(
        col(Message.session_id) == session_id,
        col(Message.diary_id) == None
    ).order_by(col(Message.created_at).asc())
    result = await db.exec(statement)
    return list(result.all())

async def create_diary(db: AsyncSession, session_id: uuid.UUID, core_emotion: str, insight: str, message_ids: list[uuid.UUID]) -> Diary:
    diary = Diary(session_id=session_id, core_emotion=core_emotion, insight=insight)
    db.add(diary)
    await db.flush()  # To get the diary.id generated

    if message_ids:
        statement = select(Message).where(col(Message.id).in_(message_ids))
        result = await db.exec(statement)
        messages = result.all()
        for msg in messages:
            msg.diary_id = diary.id
            db.add(msg)
            
    await db.commit()
    await db.refresh(diary)
    return diary

async def get_diaries_by_timeline(db: AsyncSession, session_id: uuid.UUID) -> list[Diary]:
    statement = select(Diary).where(col(Diary.session_id) == session_id).order_by(col(Diary.created_at).desc())
    result = await db.exec(statement)
    return list(result.all())

async def get_messages_by_diary(db: AsyncSession, diary_id: uuid.UUID) -> list[Message]:
    statement = select(Message).where(col(Message.diary_id) == diary_id).order_by(col(Message.created_at).asc())
    result = await db.exec(statement)
    return list(result.all())
