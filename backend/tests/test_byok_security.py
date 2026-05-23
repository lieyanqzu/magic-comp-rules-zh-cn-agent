"""BYOK（用户自带 LLM API）安全性测试。

关键不变量：
- LLMOverride.__repr__ 永远不暴露字段值（防 traceback / structlog 泄露）。
- BYOK 启用时 judge_queries.model 写占位符，绝不写用户提供的 model 名。
- BYOK 启用时绕过 LLM 响应缓存（避免跨用户串读 / 用错 model 命中）。
- max_tokens 是非敏感数值，不触发 BYOK 占位符 / 不影响缓存策略。
"""

from app.services.judge_service import LLMOverride, _BYOK_MARKER


def test_llm_override_repr_does_not_leak_values() -> None:
    """__repr__ 不应包含 api_key/base_url/model 任何字段值。"""
    o = LLMOverride(
        api_key="sk-supersecret-1234567890",
        base_url="https://private.example.com/v1",
        model="proprietary-model-name",
        max_tokens=64000,
    )
    rep = repr(o)
    assert "sk-supersecret-1234567890" not in rep
    assert "private.example.com" not in rep
    assert "proprietary-model-name" not in rep
    # 但可以表明是否激活，便于调试
    assert "byok=True" in rep
    # max_tokens 数值本身也不出现（避免歧义），只标 set/default
    assert "64000" not in rep


def test_llm_override_repr_inactive() -> None:
    o = LLMOverride()
    assert "byok=False" in repr(o)
    assert "default" in repr(o)
    # 全 None 时也不应有字段值（验证默认占位符）
    assert "None" not in repr(o)


def test_llm_override_is_active_includes_max_tokens() -> None:
    """is_active 是"任意字段被覆盖"的语义，max_tokens 也算。"""
    assert LLMOverride().is_active() is False
    assert LLMOverride(api_key="x").is_active() is True
    assert LLMOverride(base_url="x").is_active() is True
    assert LLMOverride(model="x").is_active() is True
    assert LLMOverride(max_tokens=8000).is_active() is True


def test_llm_override_is_byok_excludes_max_tokens() -> None:
    """is_byok 是"是否带了用户自带凭证"的语义，max_tokens 不算。"""
    assert LLMOverride().is_byok() is False
    assert LLMOverride(api_key="x").is_byok() is True
    assert LLMOverride(base_url="x").is_byok() is True
    assert LLMOverride(model="x").is_byok() is True
    # max_tokens 单独设置不算 BYOK，所以不绕缓存、不写占位符
    assert LLMOverride(max_tokens=8000).is_byok() is False


def test_byok_marker_is_constant() -> None:
    """占位符不应包含任何用户可控内容。"""
    assert _BYOK_MARKER == "(byok)"
    # 确保是 ASCII 常量，不是从用户输入派生
    assert all(ord(c) < 128 for c in _BYOK_MARKER)
