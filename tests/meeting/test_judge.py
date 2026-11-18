import json

import pytest

from civiccast_brief.meeting import judge as J
from civiccast_brief.script.recap import Claim


def claim(n, text, quote):
    return Claim(id=f"c{n:02d}", topic="T", text=text, timestamp="0:00:01", quote=quote)


SEP22_C30 = claim(
    1,
    "The motion to add the traffic control condition carried unanimously.",
    "Okay. And that carries unanimously.",
)
OCT20_C68 = claim(
    2,
    "The council adjourned after a motion to adjourn was approved.",
    "Okay. We have a motion to adjourn. All those in favor? I. All those opposed. Okay. "
    "We are adjourned.",
)
SEP22_C01 = claim(
    3,
    "A motion was made to adopt the minutes as corrected.",
    "I move we adopt the minutes as corrected.",
)


def answer(rows):
    return json.dumps({"rows": rows}), "receipt"


def test_overstates_flags_a_decision_the_quote_does_not_state():
    assert J.overstates(OCT20_C68.text, OCT20_C68.quote)
    assert not J.overstates("The motion passed.", "and that carries six to one")
    assert J.overstates("The plan was approved.", "if it is approved we would start in May")
    assert not J.overstates(SEP22_C01.text, SEP22_C01.quote)


def test_supported_and_grounded_claim_is_kept():
    def complete(prompt):
        return answer(
            [
                {
                    "index": 0,
                    "verdict": "Supported",
                    "evidence": "adopt the minutes as corrected",
                    "reason": "says so",
                }
            ]
        )

    judgments, calls = J.judge([SEP22_C01], complete)
    assert judgments[0].kept and judgments[0].verdict == J.SUPPORTED
    assert len(calls) == 1 and "error" not in calls[0]


def test_not_supported_claim_is_dropped():
    def complete(prompt):
        return answer(
            [
                {
                    "index": 0,
                    "verdict": "Not supported",
                    "evidence": "that carries",
                    "reason": "the quote does not name the condition",
                }
            ]
        )

    judgments, _ = J.judge([SEP22_C30], complete)
    assert not judgments[0].kept and judgments[0].verdict == J.NOT_SUPPORTED


def test_code_veto_overrides_a_supported_verdict():
    def complete(prompt):
        return answer(
            [{"index": 0, "verdict": "Supported", "evidence": "We are adjourned.", "reason": "ok"}]
        )

    judgments, _ = J.judge([OCT20_C68], complete)
    assert not judgments[0].kept
    assert judgments[0].verdict == J.NOT_SUPPORTED
    assert "decision" in judgments[0].reason


def test_supported_with_evidence_outside_the_quote_does_not_count():
    def complete(prompt):
        return answer(
            [
                {
                    "index": 0,
                    "verdict": "Supported",
                    "evidence": "the traffic control condition",
                    "reason": "context",
                }
            ]
        )

    judgments, _ = J.judge([SEP22_C30], complete)
    assert not judgments[0].kept and judgments[0].verdict == J.NEEDS_A_HUMAN


def test_missing_or_duplicate_rows_and_unknown_verdicts_are_dropped():
    def complete(prompt):
        return answer(
            [
                {"index": 1, "verdict": "Supported", "evidence": "x" * 10},
                {"index": 1, "verdict": "Supported", "evidence": "x" * 10},
                {"index": 2, "verdict": "Probably", "evidence": "adopt the minutes"},
            ]
        )

    judgments, _ = J.judge([SEP22_C30, OCT20_C68, SEP22_C01], complete)
    assert [j.kept for j in judgments] == [False, False, False]
    assert all(j.verdict == J.NEEDS_A_HUMAN for j in judgments)


def test_unreadable_answer_is_retried_once_then_drops_the_batch():
    calls = []

    def complete(prompt):
        calls.append(prompt)
        return "not json", "receipt"

    judgments, records = J.judge([SEP22_C01, SEP22_C30], complete)
    assert len(calls) == 2
    assert all(not j.kept for j in judgments)
    assert all("error" in r for r in records)


def test_retry_succeeds_on_second_attempt():
    replies = iter(
        [
            ("garbage", "r"),
            answer(
                [
                    {
                        "index": 0,
                        "verdict": "Supported",
                        "evidence": "adopt the minutes",
                        "reason": "",
                    }
                ]
            ),
        ]
    )
    judgments, records = J.judge([SEP22_C01], lambda prompt: next(replies))
    assert judgments[0].kept
    assert [r["attempt"] for r in records] == [1, 2]


def test_claims_are_judged_in_batches_and_indexes_restart_per_batch():
    claims = [
        claim(n, "A motion was made to adopt the minutes.", "I move we adopt the minutes.")
        for n in range(1, 24)
    ]
    prompts = []

    def complete(prompt):
        prompts.append(prompt)
        count = prompt.count('"index"')
        return answer(
            [
                {"index": i, "verdict": "Supported", "evidence": "adopt the minutes"}
                for i in range(count)
            ]
        )

    judgments, _ = J.judge(claims, complete, batch=10)
    assert len(prompts) == 3
    assert all(j.kept for j in judgments)


def test_prompt_holds_claim_and_quote_only():
    prompt = J.build_prompt([SEP22_C30])
    assert SEP22_C30.text in prompt and "that carries unanimously" in prompt
    assert "quote alone" in prompt


@pytest.mark.parametrize("bad", ["", "[]", '{"rows": 3}'])
def test_parse_rows_rejects_bad_answers(bad):
    with pytest.raises(ValueError):
        J.parse_rows(bad)


def test_a_garbled_last_sentence_costs_only_that_sentence():
    assert (
        J.trim_garbled_tail(
            "I needed to resign my council seat due to potential conflicts with my day job. "
            "Tonight's playing last council meeting."
        )
        == "I needed to resign my council seat due to potential conflicts with my day job."
    )
    assert J.trim_garbled_tail("One sentence only.") == ""


def test_a_claim_is_kept_on_its_clean_first_sentence_after_a_garbled_tail():
    resignation = claim(
        1,
        "A council member said they needed to resign their council seat over conflicts with "
        "their day job.",
        "I needed to resign my council seat due to potential conflicts with my day job. "
        "Tonight's playing last council meeting.",
    )
    prompts = []

    def complete(prompt):
        prompts.append(prompt)
        if "Tonight's playing" in prompt:
            return answer(
                [
                    {
                        "index": 0,
                        "verdict": "Needs a human",
                        "evidence": "Tonight's playing",
                        "reason": "Final sentence is garbled and unclear.",
                    }
                ]
            )
        return answer(
            [
                {
                    "index": 0,
                    "verdict": "Supported",
                    "evidence": "needed to resign my council seat due to potential conflicts",
                    "reason": "the clear sentence says it",
                }
            ]
        )

    judgments, calls = J.judge([resignation], complete)
    assert judgments[0].kept
    assert judgments[0].claim.quote.endswith("my day job.")
    assert len(prompts) == 2
    assert calls[-1]["count"] == 1


def test_the_judge_is_told_the_records_terms():
    prompt = J.build_prompt([SEP22_C01], ["SURA", "International Fire Code"])
    assert "OFFICIAL TERMS" in prompt
    assert "SURA" in prompt and "International Fire Code" in prompt
    assert "not garbling by itself" in prompt


def test_the_judge_is_told_a_roster_full_name_may_back_a_surname_in_the_quote():
    prompt = J.build_prompt([SEP22_C01], ["Stephanie Miller", "Heather Cracraft"])
    assert "Stephanie Miller" in prompt
    assert "Council Member Miller" in prompt
    assert "chair's call and the member's answer" in prompt
