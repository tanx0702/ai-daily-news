"""Claim-scope guard: cumulative commitments must not become one-off investments.

Regression from the real 2026-10-19 draft: Anthropic published "Investing in the
public benefit" committing **5% of shares today**, and noting that this brings the
**cumulative** total of shares dedicated to the public good to roughly **20%**
(co-founders' 80% pledge + employee match included). The title shipped as
"Anthropic 成立公益基金，将约 20% 的公司股份投入公益事业", reading as if the new
fund alone took 20% — a fourfold overstatement. The quote was verbatim and
carried a number, so every structural gate passed.
"""

from src.briefing.validator import _claim_overstates_commitment_scope


def test_cumulative_percentage_cannot_become_a_one_off_investment():
    title = "Anthropic 成立公益基金，将约 20% 的公司股份投入公益事业"
    quotes = [
        "In total, this means that roughly 20% of Anthropic's shares are committed "
        "to the public good."
    ]

    assert _claim_overstates_commitment_scope(title, quotes) is True


def test_present_commitment_wording_is_accepted():
    """The real announcement figure (5% committed today) must pass."""
    title = "Anthropic 成立公益基金，首期投入 5% 股份"
    quotes = ["5% of Anthropic's shares will be committed today."]

    assert _claim_overstates_commitment_scope(title, quotes) is False


def test_cumulative_title_against_cumulative_quote_is_fine():
    title = "Anthropic 累计约 20% 股份用于公益"
    quotes = [
        "In total, roughly 20% of Anthropic shares are committed to the public good."
    ]

    assert _claim_overstates_commitment_scope(title, quotes) is False


def test_guard_ignores_titles_without_invest_verb_or_percentage():
    cumulative = [
        "In total, roughly 20% of Anthropic shares are committed to the public good."
    ]

    assert _claim_overstates_commitment_scope("Anthropic 宣布成立公益基金", cumulative) is False
    assert _claim_overstates_commitment_scope("Anthropic 成立公益基金", cumulative) is False


def test_guard_leaves_ordinary_numbered_releases_alone():
    title = "NVIDIA 发布 Nemotron 3.5，参数量 120B"
    quotes = ["NVIDIA released Nemotron 3.5 with 120B parameters."]

    assert _claim_overstates_commitment_scope(title, quotes) is False
