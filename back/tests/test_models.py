import pytest
from pydantic import ValidationError

from rapidboxes.models import GrowthConfig, TropismConfig, validate_notify_email


def test_report_on_issue_requires_email_tropism():
    with pytest.raises(ValidationError, match="notifyEmail is required"):
        TropismConfig(experimentName="t", username="u", reportOnIssueEnabled=True)


def test_report_on_issue_requires_email_growth():
    with pytest.raises(ValidationError, match="notifyEmail is required"):
        GrowthConfig(experimentName="g", username="u", reportOnIssueEnabled=True)


def test_report_on_issue_with_valid_email_is_accepted():
    config = TropismConfig(
        experimentName="t",
        username="u",
        reportOnIssueEnabled=True,
        notifyEmail="researcher@example.com",
    )
    assert config.notifyEmail == "researcher@example.com"


def test_malformed_email_is_rejected_even_when_not_reporting():
    with pytest.raises(ValidationError, match="valid email address"):
        TropismConfig(experimentName="t", username="u", notifyEmail="not-an-email")


def test_email_not_required_when_reporting_disabled():
    config = TropismConfig(experimentName="t", username="u")
    assert config.reportOnIssueEnabled is False
    assert config.notifyEmail is None


@pytest.mark.parametrize(
    "email",
    ["a@b.co", "researcher.name+tag@sub.example.org"],
)
def test_validate_notify_email_accepts_reasonable_addresses(email):
    assert validate_notify_email(email) == email


@pytest.mark.parametrize("email", ["", "no-at-sign.com", "@missing-local.com", "missing-domain@"])
def test_validate_notify_email_rejects_malformed(email):
    with pytest.raises(ValueError):
        validate_notify_email(email)
