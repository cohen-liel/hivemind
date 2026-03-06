"""
Simulate: User selects telegram-claude-bot project with 2 agents,
then sends "find improvement opportunities in this project"
"""
import asyncio
import sys
import time

# Make sure we use the right path
sys.path.insert(0, ".")

from agent_manager import AgentManager
from config import DEFAULT_AGENTS

PROJECT_DIR = "/Users/lielcohen/Downloads/telegram-claude-bot"
PROJECT_NAME = "telegram-claude-bot"

messages_received = []

async def on_update(text: str):
    """Simulate Telegram — just print to console."""
    messages_received.append(text)
    print(f"\n{'='*60}")
    print(f"📱 TELEGRAM UPDATE:")
    print(f"{'='*60}")
    print(text[:2000])
    if len(text) > 2000:
        print(f"... ({len(text)} chars total)")
    print(f"{'='*60}\n")


async def main():
    # Build 2-agent config (architect + developer) — same as the bot does
    agents_config = DEFAULT_AGENTS[:2]

    print(f"🚀 Creating AgentManager with {len(agents_config)} agents:")
    for a in agents_config:
        print(f"   - {a['name']} ({a['role']})")
    print(f"📁 Project dir: {PROJECT_DIR}")
    print()

    manager = AgentManager(
        project_name=PROJECT_NAME,
        project_dir=PROJECT_DIR,
        agents_config=agents_config,
        on_update=on_update,
    )

    user_message = (
        "Review this Telegram bot project and find improvement opportunities. "
        "Focus on: code quality, error handling, architecture, and missing features. "
        "Give specific actionable suggestions."
    )

    print(f"📨 User message: {user_message}")
    print(f"⏳ Starting session (this may take a few minutes)...")
    print()

    await manager.start_session(user_message)

    # Wait for the background task to complete (max 5 minutes)
    start = time.time()
    while manager.is_running and (time.time() - start) < 300:
        await asyncio.sleep(2)
        elapsed = int(time.time() - start)
        if elapsed % 30 == 0 and elapsed > 0:
            print(f"⏳ Still working... ({elapsed}s elapsed, turn {manager.turn_count})")

    # Show results
    print(f"\n{'#'*60}")
    print(f"SIMULATION COMPLETE")
    print(f"{'#'*60}")
    print(f"Turns: {manager.turn_count}")
    print(f"Cost: ${manager.total_cost_usd:.4f}")
    print(f"Messages in log: {len(manager.conversation_log)}")
    print(f"Running: {manager.is_running}")
    print(f"Paused: {manager.is_paused}")
    print()

    if manager.conversation_log:
        print(f"📜 Conversation Log:")
        for i, msg in enumerate(manager.conversation_log):
            preview = msg.content[:500]
            if len(msg.content) > 500:
                preview += "..."
            print(f"\n--- Message {i+1}: {msg.agent_name} ({msg.role}) | ${msg.cost_usd:.4f} ---")
            print(preview)
    else:
        print("❌ No messages in conversation log!")

    # Stop cleanly
    if manager.is_running or manager.is_paused:
        await manager.stop()


if __name__ == "__main__":
    asyncio.run(main())
