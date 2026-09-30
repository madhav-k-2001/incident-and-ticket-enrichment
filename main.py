import asyncio
import os
import sys

from agents import Agent, Runner
from agents.extensions.memory import SQLAlchemySession

agent = Agent(
    name="Incident Copilot",
    instructions="You help plant operators investigate alarms and draft incident tickets.",
)


def get_session(session_id: str) -> SQLAlchemySession:
    """Chat history for one conversation, persisted in PostgreSQL."""
    return SQLAlchemySession.from_url(
        session_id,
        url=os.environ["DATABASE_URL"],  # e.g. postgresql+asyncpg://user:pass@host:5432/db
        create_tables=True,
    )


async def chat(session_id: str, message: str) -> str:
    session = get_session(session_id)
    try:
        result = await Runner.run(agent, message, session=session)
        return result.final_output
    finally:
        await session.engine.dispose()


if __name__ == "__main__":
    session_id, message = sys.argv[1], " ".join(sys.argv[2:])
    print(asyncio.run(chat(session_id, message)))
