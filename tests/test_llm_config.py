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
