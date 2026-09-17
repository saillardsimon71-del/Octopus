"""Tests sans réseau pour le contrat vidéo cloud."""
from octopus.video.contract import Artifact, VideoContractError, VideoJob, VideoStatus


def sample():
    return {
        "schema_version": "1",
        "job_id": "job-123",
        "offer_id": "offer-123",
        "template": "forge-v4",
        "language": "fr",
        "duration_seconds": 24,
        "script": {"segments": [{"text": "Bonjour", "role": "hook"}]},
        "voice": {"provider": "chatterbox", "voice": "vivienne-fr"},
        "assets": [],
        "quality": {"target_lufs": -14, "min_lra": 5, "min_score": 24},
    }


def test_roundtrip():
    job = VideoJob.from_dict(sample())
    assert job.job_id == "job-123"
    assert job.to_dict()["schema_version"] == "1"


def test_reject_missing_identity():
    payload = sample()
    del payload["job_id"]
    try:
        VideoJob.from_dict(payload)
    except VideoContractError:
        pass
    else:
        raise AssertionError("missing job_id must fail")


def test_reject_secret():
    payload = sample()
    payload["voice"]["api_key"] = "do-not-send"
    try:
        VideoJob.from_dict(payload)
    except VideoContractError:
        pass
    else:
        raise AssertionError("secret must fail")


def test_reject_shell_unsafe_template():
    payload = sample()
    payload["template"] = "CashShort/../../rm"
    try:
        VideoJob.from_dict(payload)
    except VideoContractError:
        pass
    else:
        raise AssertionError("unsafe template must fail")


def test_artifact_key_roundtrip():
    artifact = Artifact("final.mp4", "https://signed.invalid", "video", "video/mp4", "abc", "offer/job/final.mp4")
    value = artifact.to_dict()
    assert value["key"] == "offer/job/final.mp4"


def test_status_values_are_stable():
    assert VideoStatus.QUEUED.value == "QUEUED"
    assert VideoStatus.RENDERING.value == "RENDERING"
    assert VideoStatus.COMPLETED.value == "COMPLETED"
    assert VideoStatus.EXPIRED.value == "EXPIRED"
