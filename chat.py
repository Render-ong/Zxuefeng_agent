"""一键启动命令行对话 — python chat.py

支持两种配置方式（优先级：环境变量 > .env 文件 > 交互式输入）：
  方式 1：设置环境变量后运行
    $env:LLM_API_KEY="sk-xxx"; python chat.py

  方式 2：复制 .env.example 为 .env 并填入配置后运行
    cp .env.example .env   # 编辑填入 Key
    python chat.py

  方式 3：直接运行，交互式输入 API Key
    python chat.py
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv():
    """从项目根目录的 .env 文件加载环境变量（不覆盖已有的）"""
    env_path = os.path.join(_HERE, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if k and k not in os.environ:
                os.environ[k] = v


def _ensure_config():
    """确保 LLM 配置就绪，缺失则交互式提示输入"""
    key = os.environ.get("LLM_API_KEY") or os.environ.get("LLM_KEY") or os.environ.get("DEEPSEEK_KEY", "")
    if key:
        return

    print("=" * 50)
    print("  AI雪峰升学指导 — 命令行对话")
    print("=" * 50)
    print()
    print("首次使用需要配置 LLM API Key")
    print("（也可复制 .env.example 为 .env 填写配置，免去每次输入）")
    print()

    key = input("请输入 API Key (sk-xxx): ").strip()
    if not key:
        print("未输入 Key，退出。")
        sys.exit(1)
    os.environ["LLM_API_KEY"] = key

    url = input("API Base URL [https://api.deepseek.com]: ").strip()
    if url:
        os.environ["LLM_API_URL"] = url

    model = input("模型名称 [deepseek-v4-flash]: ").strip()
    if model:
        os.environ["LLM_MODEL"] = model

    tavily = input("Tavily Key（可选，直接回车跳过）: ").strip()
    if tavily:
        os.environ["TAVILY_KEY"] = tavily

    print()


def main():
    _load_dotenv()
    _ensure_config()

    # 解压知识库（首次运行时 .gz → .db）
    db_gz = os.path.join(_HERE, "GraphRAG", "admission_clean.db.gz")
    db_file = os.path.join(_HERE, "GraphRAG", "admission_clean.db")
    if os.path.exists(db_gz) and not os.path.exists(db_file):
        import gzip, shutil
        print("首次运行，解压知识库...")
        with gzip.open(db_gz, "rb") as f_in, open(db_file, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        print("解压完成。\n")

    # 验证配置是否生效
    sys.path.insert(0, os.path.join(_HERE, "langgraph"))
    from llm_config import get_api_config
    cfg = get_api_config()
    key = cfg.get("key", "")
    if not key:
        print("错误：API Key 未生效，请检查环境变量或 .env 文件")
        sys.exit(1)
    print(f"API Key: {key[:8]}...{key[-4:]}  模型: {cfg.get('model')}  地址: {cfg.get('url')}")
    print()

    # 启动交互式对话
    from run import main as run_main
    run_main()


if __name__ == "__main__":
    main()
