"""API 配置模块 — 集中管理 LLM API 配置

优先级：环境变量 > 配置文件 > 默认值
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONFIG_FILE = os.path.join(_HERE, "api_config.json")
# ponytail: 接入 GraphRAG 后，config.json 作为 fallback 配置源，统一前端设置页写入的配置。
# 升级路径：迁环境变量/配置中心后此 fallback 废弃。
_GRAPHRAG_CONFIG = os.path.join(_HERE, "..", "GraphRAG", "config.json")


def _default_config() -> dict:
    # ponytail: 密钥不再硬编码入仓，必须从环境变量或 api_config.json 读取。
    # 缺失时返回空串，调用方应显式校验并报错，避免静默用错 key。
    # 升级路径：生产部署强制读 env，缺失即 raise，不再 fallback 到文件。
    # 环境变量名与 api_server.py 对齐：LLM_API_KEY / LLM_API_URL 优先，
    # 旧名 LLM_KEY / LLM_URL / DEEPSEEK_KEY 保留兼容（.env.example 统一用新名）。
    return {
        "llm": {
            "url": os.environ.get("LLM_API_URL") or os.environ.get("LLM_URL", ""),
            "model": os.environ.get("LLM_MODEL", ""),
            "key": os.environ.get("LLM_API_KEY") or os.environ.get("LLM_KEY") or os.environ.get("DEEPSEEK_KEY", ""),
        },
        "tavily": {
            "key": os.environ.get("TAVILY_KEY", ""),
        },
    }


def load_config() -> dict:
    """加载配置，优先级：环境变量 > api_config.json > GraphRAG/config.json > 内置默认值"""
    config = _default_config()
    _BUILTIN_DEFAULTS = {
        "llm": {"url": "https://api.deepseek.com", "model": "deepseek-v4-flash"},
    }

    for cfg_path in (_CONFIG_FILE, _GRAPHRAG_CONFIG):
        if not os.path.exists(cfg_path):
            continue
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                file_config = json.load(f)
            if "llm_api_key" in file_config or "llm_api_url" in file_config:
                file_config = {
                    "llm": {
                        "url": file_config.get("llm_api_url", ""),
                        "model": file_config.get("llm_model", ""),
                        "key": file_config.get("llm_api_key", ""),
                    },
                    "tavily": {"key": file_config.get("tavily_key", "")},
                }
            for provider in ("llm", "tavily"):
                if provider in file_config:
                    for k, v in file_config[provider].items():
                        if v and not config[provider].get(k):
                            config[provider][k] = v
        except Exception:
            pass

    # 内置默认值兜底（环境变量和文件都没设时）
    for provider, defaults in _BUILTIN_DEFAULTS.items():
        for k, v in defaults.items():
            if not config[provider].get(k):
                config[provider][k] = v

    return config


def save_config(config: dict) -> None:
    """保存配置到文件"""
    with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def get_api_config() -> dict:
    """获取当前 API 配置（供 agent 使用）"""
    config = load_config()
    return {
        "url": config["llm"]["url"],
        "model": config["llm"]["model"],
        "key": config["llm"]["key"],
        "tavily": config["tavily"]["key"],
    }


def update_api_config(provider: str, **kwargs) -> dict:
    """更新 API 配置

    参数
    ----
    provider : str
        "llm" 或 "tavily"
    **kwargs :
        url, model, key 等配置项

    返回
    ----
    更新后的完整配置
    """
    config = load_config()
    if provider not in config:
        config[provider] = {}

    for k, v in kwargs.items():
        if v:  # 只更新非空值
            config[provider][k] = v

    save_config(config)
    return get_api_config()


def check_config() -> dict:
    """检查配置状态"""
    config = get_api_config()
    return {
        "llm_configured": bool(config.get("key")),
        "tavily_configured": bool(config.get("tavily")),
        "llm_url": config.get("url", ""),
        "llm_model": config.get("model", ""),
    }
