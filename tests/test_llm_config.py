from src.llm_config import LLMConfig, structured_llm_request_options


def test_glm_5_3_flash_uses_low_reasoning_effort():
    config = LLMConfig(
        "key",
        " GLM-5.3-FLASH ",
        "https://open.bigmodel.cn/api/paas/v4",
    )

    assert structured_llm_request_options(config) == {
        "extra_body": {"reasoning_effort": "low"}
    }


def test_other_models_do_not_receive_provider_specific_options():
    config = LLMConfig("key", "deepseek-chat", "https://api.deepseek.com/v1")

    assert structured_llm_request_options(config) == {}


def test_router_namespaced_glm_still_gets_low_reasoning_effort():
    """A provider-prefixed model id must keep the Zhipu reasoning flag.

    Regression from the 9router switch: the model became ``cbcn/glm-5.3-flash``,
    the old exact-string compare missed it, and without ``reasoning_effort=low``
    the reasoning model spent its whole budget on hidden reasoning and returned
    empty content — 46 items in one edition counted as ``content_llm_unavailable``
    and the run blocked.
    """
    for model in ("cbcn/glm-5.3-flash", "cbai/glm-5.3-flash", "GLM-5.3-Flash"):
        config = LLMConfig("key", model, "http://9router:20128/v1")
        assert structured_llm_request_options(config) == {
            "extra_body": {"reasoning_effort": "low"}
        }, model


def test_other_namespaced_models_do_not_receive_the_zhipu_option():
    for model in ("cbcn/deepseek-v4.1-flash", "cbai/kimi-k2.6", "openai/gpt-4o"):
        config = LLMConfig("key", model, "http://9router:20128/v1")
        assert structured_llm_request_options(config) == {}, model
