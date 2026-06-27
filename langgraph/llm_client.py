"""公共 LLM 调用客户端 — 统一超时/错误处理

用法：
    from llm_client import call_llm
    reply = call_llm(api_config, messages)
"""
import json
import logging
import urllib.request
import urllib.error

log = logging.getLogger("llm_client")

# ponytail: 统一超时 60s。小程序 wx.request 超时 90s，后端 60s 留 30s 余量给网络+Nginx。
LLM_TIMEOUT = 60


def call_llm(api_config: dict, messages: list, temperature: float = 0.7,
             max_tokens: int = 2000, timeout: int = LLM_TIMEOUT) -> str:
    """调用 DeepSeek/OpenAI 兼容 API，返回 assistant 内容。

    Args:
        api_config: {"url": "...", "key": "...", "model": "..."}
        messages: OpenAI 格式消息列表
        temperature: 采样温度
        max_tokens: 最大生成 token 数
        timeout: 请求超时秒数（默认 60s）

    Returns:
        LLM 生成的文本内容

    Raises:
        urllib.error.URLError: 网络/超时错误
        KeyError: 响应格式异常
    """
    url = api_config.get("url", "https://api.deepseek.com").rstrip("/") + "/v1/chat/completions"
    payload = json.dumps({
        "model": api_config.get("model", "deepseek-chat"),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_config.get('key', '')}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    return data["choices"][0]["message"]["content"]