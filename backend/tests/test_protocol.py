import json

import pytest

from voice_app.protocol import ProtocolError, event, parse_client_event


@pytest.mark.parametrize(
    ("payload", "expected_type"),
    [
        (
            {
                "type": "session.start",
                "client_id": "phone-123",
                "voice": "serena",
                "generation_id": 0,
            },
            "session.start",
        ),
        (
            {"type": "session.configure", "voice": "uncle_fu", "generation_id": 0},
            "session.configure",
        ),
        (
            {
                "type": "audio.start",
                "turn_id": 1,
                "generation_id": 1,
                "speaker_token": "x" * 43,
            },
            "audio.start",
        ),
        ({"type": "audio.commit", "turn_id": 1, "generation_id": 1}, "audio.commit"),
        ({"type": "response.cancel", "generation_id": 2}, "response.cancel"),
        ({"type": "session.end", "generation_id": 2}, "session.end"),
        ({"type": "ping", "generation_id": 2, "nonce": "abc"}, "ping"),
        (
            {
                "type": "client.metrics",
                "generation_id": 2,
                "turn_id": 1,
                "stage": "first_audio",
                "commit_to_first_audio_ms": 824.5,
            },
            "client.metrics",
        ),
    ],
)
def test_parse_every_client_event(payload, expected_type):
    parsed = parse_client_event(json.dumps(payload))
    assert parsed.type == expected_type
    assert parsed.generation_id >= 0


@pytest.mark.parametrize("turn_id", [-1, 0, "one", None])
def test_turn_id_must_be_positive_integer(turn_id):
    raw = json.dumps(
        {
            "type": "audio.start",
            "turn_id": turn_id,
            "generation_id": 1,
            "speaker_token": "x" * 43,
        }
    )
    with pytest.raises(ProtocolError) as caught:
        parse_client_event(raw)
    assert caught.value.code == "invalid_event"


def test_audio_start_requires_a_bounded_speaker_token():
    for token in (None, "short", "x" * 257):
        raw = json.dumps(
            {
                "type": "audio.start",
                "turn_id": 1,
                "generation_id": 1,
                **({} if token is None else {"speaker_token": token}),
            }
        )
        with pytest.raises(ProtocolError):
            parse_client_event(raw)


def test_unknown_event_has_stable_error_code():
    with pytest.raises(ProtocolError) as caught:
        parse_client_event('{"type":"audio.nope","generation_id":1}')
    assert caught.value.code == "unknown_event"


@pytest.mark.parametrize("voice", ["unknown", "Serena", "../../voice", ""])
def test_session_voice_must_be_an_official_lowercase_preset(voice):
    raw = json.dumps(
        {
            "type": "session.configure",
            "voice": voice,
            "generation_id": 1,
        }
    )
    with pytest.raises(ProtocolError) as caught:
        parse_client_event(raw)
    assert caught.value.code == "invalid_event"


@pytest.mark.parametrize("raw", ["not-json", "[]", "{}", '{"type":"ping","generation_id":-1}'])
def test_malformed_event_has_stable_error_code(raw):
    with pytest.raises(ProtocolError) as caught:
        parse_client_event(raw)
    assert caught.value.code == "invalid_event"


def test_server_event_has_type_and_generation():
    payload = event("state", generation_id=4, state="thinking")
    assert payload == {"type": "state", "generation_id": 4, "state": "thinking"}


def test_event_rejects_invalid_generation():
    with pytest.raises(ValueError):
        event("state", generation_id=-1, state="idle")


@pytest.mark.parametrize(
    "payload",
    [
        {"commit_to_first_audio_ms": -1},
        {"commit_to_first_audio_ms": 3_600_001},
        {"commit_to_first_audio_ms": float("nan")},
        {"unknown_ms": 4},
    ],
)
def test_client_metrics_accept_only_bounded_known_values(payload):
    raw = json.dumps(
        {
            "type": "client.metrics",
            "generation_id": 2,
            "turn_id": 1,
            "stage": "complete",
            **payload,
        }
    )
    with pytest.raises(ProtocolError):
        parse_client_event(raw)
