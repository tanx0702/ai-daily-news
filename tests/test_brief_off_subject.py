"""Off-subject summary guard: a brief must not report a rival's news.

Regression from the real 2026-10-20 draft. The display title was
"Claude 新模型曝光：能跳出思维定势，修 Bug 治标又治本" and the brief was
"同期 OpenAI 只推出一个 6.1 Terra 相关内容。" Both sentences come from one 量子位
roundup whose evidence text is title + teaser, so the quote was verbatim and
carried a number. Every existing gate passed, and the draft attached a *rival's*
upcoming release to the Claude headline.

The guard only compares registered company names. Model names are deliberately
excluded so a legitimate same-company comparison ("GPT-6.1 Sol 在 low 档达到 86.6%
准确率，而 GPT-5.6 Sol 只有 5.4%") still passes.
"""

from src.briefing.validator import _summary_off_subject


def test_rival_release_is_not_a_summary_of_this_headline():
    assert (
        _summary_off_subject(
            "同期 OpenAI 只推出一个 6.1 Terra 相关内容",
            source_title="Claude新模型曝光！能跳出思维定势，修Bug治标又治本",
            title_claim="Claude 新模型曝光：能跳出思维定势，修 Bug 治标又治本",
        )
        is True
    )


def test_same_company_summary_is_kept():
    assert (
        _summary_off_subject(
            "该模型基于 Gemma 4 构建，采用 Apache 2.0 许可证发布，是一个紧凑的多模态嵌入模型",
            source_title="Google Releases EmbeddingGemma 2 Open-Weights Model",
            title_claim="Google 推出 EmbeddingGemma 2 开放权重模型",
        )
        is False
    )


def test_same_company_comparison_against_own_older_model_is_kept():
    assert (
        _summary_off_subject(
            "GPT-6.1 Sol 在 low 档达到 86.6% 准确率，而 GPT-5.6 Sol 只有 5.4%",
            source_title="OpenAI improves GPT-6.1 on COBOL migration",
            title_claim="OpenAI 提升 GPT-6.1 在 COBOL 迁移上的准确率",
        )
        is False
    )


def test_summary_without_any_company_name_is_never_rejected():
    """A summary may be purely quantitative; absence of a company is not a fault."""
    assert (
        _summary_off_subject(
            "该模型在 130000 个工程任务上完成部署",
            source_title="DoorDash Flux agent completes tasks",
            title_claim="DoorDash 的 Flux 智能体已完成 130000 个工程任务",
        )
        is False
    )


def test_chinese_company_name_maps_to_the_latin_entity():
    """``谷歌`` and ``Google`` are one company, not two."""
    assert (
        _summary_off_subject(
            "谷歌同步发布了 Gemini 4 Carbon 的内测版本",
            source_title="Google rolls out Gemini 4 Carbon internally",
            title_claim="Google 内部开放 Gemini 4 Carbon 测试",
        )
        is False
    )
