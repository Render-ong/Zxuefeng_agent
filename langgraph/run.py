"""快速测试入口 — 命令行交互，支持多用户多对话"""
import sys
import os
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from agent import run, get_user_conversations, get_conversation_detail
from llm_config import check_config


def main():
    # 检查配置
    status = check_config()
    if not status["llm_configured"]:
        print("⚠️  未配置 LLM API Key")
        print("   方式1: 设置环境变量 $env:LLM_KEY='sk-xxx'")
        print("   方式2: 运行 python config.py 进行配置")
        print()

    # 模拟用户 ID（实际使用时从微信获取）
    user_id = os.environ.get("USER_ID", f"user_{uuid.uuid4().hex[:8]}")
    conversation_id = None

    # 单次命令模式: python run.py "消息内容"
    if len(sys.argv) > 1:
        msg = " ".join(sys.argv[1:])
        result = run(
            message=msg,
            user_id=user_id,
        )
        _print_result(result)
        return

    # 交互模式
    print("=" * 50)
    print("  张雪峰志愿填报 Agent (LangGraph版)")
    print(f"  用户: {user_id}")
    print("  命令：")
    print("    /new     - 开始新对话")
    print("    /list    - 查看历史对话")
    print("    /switch  - 切换对话")
    print("    quit     - 退出")
    print("=" * 50)
    print()

    while True:
        try:
            msg = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break
        if not msg:
            continue

        # 处理命令
        if msg.lower() in ("quit", "exit", "q"):
            print("再见！")
            break

        if msg == "/new":
            conversation_id = None
            print("已开始新对话")
            continue

        if msg == "/list":
            convs = get_user_conversations(user_id)
            if convs:
                print("\n历史对话：")
                for i, conv in enumerate(convs, 1):
                    print(f"  {i}. [{conv['conversation_id']}] {conv['title']} ({conv['message_count']}条)")
            else:
                print("暂无历史对话")
            print()
            continue

        if msg.startswith("/switch"):
            parts = msg.split()
            if len(parts) > 1:
                conversation_id = parts[1]
                print(f"已切换到对话: {conversation_id}")
            else:
                print("用法: /switch <conversation_id>")
            continue

        # 发送消息
        result = run(
            message=msg,
            user_id=user_id,
            conversation_id=conversation_id,
        )

        # 保存 conversation_id 用于后续对话
        conversation_id = result.get("conversation_id")

        _print_result(result)


def _print_result(result):
    print()
    debug = result.get("debug", {})
    print(f"[意图] {debug.get('intent', '?')}")
    print(f"[对话] {result.get('conversation_id', '?')}")
    profile = result.get("profile", {})
    if profile:
        filled = [k for k, v in profile.items() if v]
        print(f"[画像] 已收集 {len(filled)}/14 项")
    if result.get("needs_ask"):
        print(f"[追问] {result.get('ask_questions', [])}")
    print()
    print(f"Agent: {result.get('reply', '(空回复)')}")
    print()
    print("-" * 50)
    print()


if __name__ == "__main__":
    main()
