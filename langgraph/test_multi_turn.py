"""测试脚本 — 验证单用户多轮对话是否正常"""
import sys
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from agent import run


def test_multi_turn():
    """测试多轮对话"""
    user_id = "test_user_001"

    print("=" * 60)
    print("  测试：单用户多轮对话")
    print("=" * 60)

    # 第一轮：用户说想学计算机
    print("\n[第1轮] 用户: 我想学计算机，湖北的，580分")
    result1 = run(
        message="我是湖北考生，物理类，580分，位次28000，想学计算机，毕业想找工作，普通工薪家庭",
        user_id=user_id,
    )
    _d1 = result1.get("debug", {})
    print(f"[意图] {_d1.get('intent')}")
    print(f"[对话ID] {result1['conversation_id']}")
    print(f"[画像] {result1['profile']}")
    print(f"[追问] {result1.get('ask_questions', [])}")
    print(f"[回复] {result1['reply'][:200]}...")

    # 第二轮：追问学校细节
    print("\n[第2轮] 用户: 武汉工程大学怎么样？")
    result2 = run(
        message="武汉工程大学怎么样？",
        user_id=user_id,
        conversation_id=result1["conversation_id"],  # 继续同一对话
    )
    _d2 = result2.get("debug", {})
    print(f"[意图] {_d2.get('intent')}")
    print(f"[画像] {result2['profile']}")
    print(f"[回复] {result2['reply'][:200]}...")

    # 第三轮：追问就业
    print("\n[第3轮] 用户: 计算机毕业好找工作吗？")
    result3 = run(
        message="计算机毕业好找工作吗？",
        user_id=user_id,
        conversation_id=result1["conversation_id"],
    )
    _d3 = result3.get("debug", {})
    print(f"[意图] {_d3.get('intent')}")
    print(f"[回复] {result3['reply'][:200]}...")

    print("\n" + "=" * 60)
    print("  测试完成！")
    print("=" * 60)


if __name__ == "__main__":
    test_multi_turn()
