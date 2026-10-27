"""健身教练智能体 - 命令行入口。

会话历史写入 SQLite，进程重启后仍可继续对话。
"""

from __future__ import annotations

import asyncio
import sys

from app.core.agent import Settings, create_fitness_agent
from app.core.prompts import WELCOME_MESSAGE
from app.core.streaming import astream_agent_events
from app.storage.store import ConversationStore


CLI_SESSION_ID = "cli-default"


async def main() -> int:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    settings = Settings.from_env()
    store = ConversationStore(settings.db_path)
    if store.get_session(CLI_SESSION_ID) is None:
        store.create_session("CLI 对话", session_id=CLI_SESSION_ID)

    try:
        agent = await create_fitness_agent(settings)
    except Exception as exc:
        print(f"Agent 初始化失败: {exc}")
        return 2

    print("=" * 50)
    print("🏋️ AI 健身教练 v1.2")
    print("=" * 50)
    print(WELCOME_MESSAGE)
    print("输入 /new 开始新会话，输入 退出 结束。\n")

    while True:
        try:
            user_input = input("👤 你: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n💪 训练辛苦了！下次再见！")
            break

        if user_input.lower() in {"退出", "exit", "quit", "q"}:
            print("💪 感谢使用 AI 健身教练，再见！")
            break
        if not user_input:
            continue
        if user_input == "/new":
            store.delete_session(CLI_SESSION_ID)
            store.create_session("CLI 对话", session_id=CLI_SESSION_ID)
            print("已开始新会话。\n")
            continue

        history = store.get_messages(CLI_SESSION_ID, limit=settings.history_limit)
        store.add_message(CLI_SESSION_ID, "user", user_input)
        messages = [(item["role"], item["content"]) for item in history]
        messages.append(("user", user_input))

        print("\n🏋️ 教练: ", end="", flush=True)
        chunks: list[str] = []
        try:
            async for event in astream_agent_events(agent, messages):
                if event["type"] == "token":
                    chunks.append(event["text"])
                    print(event["text"], end="", flush=True)
                elif event["type"] == "tool_call":
                    print(f"\n  ↳ 调用工具 {event['name']}", flush=True)
        except Exception as exc:
            print(f"\n❌ 出错了: {exc}")
            continue

        answer = "".join(chunks).strip()
        if answer:
            store.add_message(CLI_SESSION_ID, "assistant", answer)
        print("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
