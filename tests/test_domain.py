from outly.domain.enums import CampaignStatus
from outly.domain.preprocessing import PreprocessOptions, preprocess_email_html
from outly.domain.rotation import PoolSender, assign_senders_round_robin
from outly.domain.scheduling import compute_jittered_delay, compute_send_offsets
from outly.domain.state_machine import is_valid_transition
from outly.domain.templating import parse_variables, resolve_variables
from outly.domain.validation import validate_sequence_steps


def test_parse_variables_unique():
    assert parse_variables("Hi {{name}}, {{name}} at {{company}}") == ["name", "company"]


def test_resolve_variables_case_insensitive():
    result = resolve_variables("Hi {{Name}} from {{company}}", {"name": "Ada", "COMPANY": "ACME"})
    assert result == "Hi Ada from ACME"


def test_resolve_variables_keeps_unknown_tokens():
    assert resolve_variables("Hi {{name}}", {}) == "Hi {{name}}"


def test_round_robin_even_distribution():
    senders = [PoolSender("a", 0), PoolSender("b", 1)]
    assignments = assign_senders_round_robin(senders, 5)
    assert assignments == ["a", "b", "a", "b", "a"]


def test_round_robin_respects_daily_limits():
    senders = [PoolSender("a", 0, daily_limit=1), PoolSender("b", 1, daily_limit=2)]
    assignments = assign_senders_round_robin(senders, 5)
    assert assignments.count("a") == 1
    assert assignments.count("b") == 2
    assert len(assignments) == 3


def test_state_machine_transitions():
    assert is_valid_transition(CampaignStatus.SCHEDULED, CampaignStatus.SENDING)
    assert is_valid_transition(CampaignStatus.PAUSED, CampaignStatus.SENDING)
    assert not is_valid_transition(CampaignStatus.COMPLETED, CampaignStatus.SENDING)
    assert not is_valid_transition(CampaignStatus.CANCELLED, CampaignStatus.PAUSED)


def test_jittered_delay_bounds():
    for _ in range(100):
        delay = compute_jittered_delay(10)
        assert 7 <= delay <= 13


def test_send_offsets_monotonic_and_bounded():
    offsets = compute_send_offsets(10, delay_seconds=5, hourly_limit=60, sender_count=1)
    assert offsets[0] == 0
    assert all(later > earlier for earlier, later in zip(offsets, offsets[1:]))
    for earlier, later in zip(offsets, offsets[1:]):
        gap = later - earlier
        assert 5 <= gap <= 84


def test_preprocess_injects_pixel_and_rewrites_links():
    html = '<body><a href="https://example.com">link</a></body>'
    result = preprocess_email_html(
        html,
        PreprocessOptions(
            email_job_id="job1",
            tracking_base_url="http://track",
            track_opens=True,
            track_clicks=True,
        ),
    )
    assert 'http://track/track/click/job1?url=https%3A%2F%2Fexample.com' in result
    assert 'http://track/track/open/job1' in result
    assert result.index("/track/open/") < result.index("</body>")


def test_preprocess_skips_mailto_and_anchors():
    html = '<a href="mailto:a@b.c">m</a><a href="#top">t</a>'
    result = preprocess_email_html(
        html,
        PreprocessOptions("job1", "http://track", track_opens=False, track_clicks=True),
    )
    assert "mailto:a@b.c" in result
    assert '#top' in result
    assert "/track/click/" not in result


def test_sequence_validation():
    assert validate_sequence_steps([]).valid
    assert validate_sequence_steps(
        [{"subject": "s", "body": "b", "waitDays": 1}]
    ).valid
    assert not validate_sequence_steps(
        [{"subject": "", "body": "b", "waitDays": 1}]
    ).valid
    assert not validate_sequence_steps(
        [{"subject": "s", "body": "b", "waitDays": 0}]
    ).valid
    too_many = [{"subject": "s", "body": "b", "waitDays": 1}] * 6
    assert not validate_sequence_steps(too_many).valid
