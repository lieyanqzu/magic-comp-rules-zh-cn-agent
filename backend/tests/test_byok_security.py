"""BYOK（用户自带 LLM API）安全性测试。

关键不变量：
- LLMOverride.__repr__ 永远不暴露字段值（防 traceback / structlog 泄露）。
- BYOK 启用时 judge_queries.model 写占位符，绝不写用户提供的 model 名。
- BYOK 启用时绕过 LLM 响应缓存（避免跨用户串读 / 用错 model 命中）。
"""

from app.services.judge_service import LLMOverride, _BYOK_MARKER


def test_llm_override_repr_does_not_leak_values() -> None:
    """__repr__ 不应包含 api_key/base_url/model 任何字段值。"""
    o = LLMOverride(
        api_key="sk-supersecret-1234567890",
        base_url="https://private.example.com/v1",
        model="proprietary-model-name",
    )
    rep = repr(o)
    assert "sk-supersecret-1234567890" not in rep
    assert "private.example.com" not in rep
    assert "proprietary-model-name" not in rep
    # 但可以表明是否激活，便于调试
    assert "active=True" in rep


def test_llm_override_repr_inactive() -> None:
    o = LLMOverride()
    assert "active=False" in repr(o)
    # 全 None 时也不应有字段值（验证默认占位符）
    assert "None" not in repr(o)


def test_llm_override_is_active() -> None:
    assert LLMOverride().is_active() is False
    assert LLMOverride(api_key="x").is_active() is True
    assert LLMOverride(base_url="x").is_active() is True
    assert LLMOverride(model="x").is_active() is True


def test_byok_marker_is_constant() -> None:
    """占位符不应包含任何用户可控内容。"""
    assert _BYOK_MARKER == "(byok)"
    # 确保是 ASCII 常量，不是从用户输入派生
    assert all(ord(c) < 128 for c in _BYOK_MARKER)
