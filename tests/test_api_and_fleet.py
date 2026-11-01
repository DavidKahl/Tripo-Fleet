import json

import pytest
from PIL import Image

from tripo_fleet import fleet
from tripo_fleet.api import TripoClient, TripoError


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.text = json.dumps(body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def test_retries_rate_limit_then_returns_data():
    session = FakeSession([FakeResponse(429, {"code": 2000}, {"Retry-After": "3"}),
                           FakeResponse(200, {"code": 0, "data": {"balance": 900.0, "frozen": 0}})])
    slept = []
    client = TripoClient("k", "https://api.test/v3", session=session, sleep=slept.append)
    assert client.balance()["balance"] == 900.0
    assert slept == [3.0]
    assert session.headers["Authorization"] == "Bearer k"


def test_insufficient_balance_raises_without_retry():
    session = FakeSession([FakeResponse(403, {"code": 2010, "message": "insufficient", "suggestion": "Top up"})])
    client = TripoClient("k", "https://api.test/v3", session=session, sleep=lambda s: None)
    with pytest.raises(TripoError) as err:
        client.create_multiview({})
    assert err.value.code == 2010 and len(session.calls) == 1


def write_image(path, size=512):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (size, size), "white").save(path)


def test_generation_submission_is_not_retried_on_server_error():
    session = FakeSession([FakeResponse(503, {"code": 5000, "message": "unavailable"})])
    client = TripoClient("k", "https://api.test/v3", session=session, sleep=lambda s: None)
    with pytest.raises(TripoError):
        client.create_multiview({"model": "P2-20260801", "quad": True, "face_limit": 20000})
    assert len(session.calls) == 1


def test_fleet_load_payload_and_checks(tmp_path):
    write_image(tmp_path / "concepts" / "taxi_city" / "front.png")
    write_image(tmp_path / "concepts" / "taxi_city" / "back.png")
    write_image(tmp_path / "concepts" / "courier_sprint" / "front.png", size=128)
    (tmp_path / "fleet.json").write_text(json.dumps({
        "generation": {"model": "P1-20260311", "bogus": 1},
        "vehicles": [
            {"id": "taxi_city", "family": "taxi", "length_m": 6, "width_m": 2.9, "height_m": 2.4,
             "generation": {"face_limit": 8000}},
            {"id": "courier_sprint", "family": "courier", "length_m": 4.5, "width_m": 2, "height_m": 1.6},
        ],
    }))
    taxi, courier = fleet.load(tmp_path / "fleet.json")
    assert taxi.payload({"front": "t1", "back": "t2"}) == {
        "inputs": [{"front": "t1"}, {"back": "t2"}], "model": "P1-20260311", "texture": True, "pbr": True,
        "model_seed": 0, "texture_seed": 0, "face_limit": 8000,
    }
    assert any("unknown generation fields" in p for p in taxi.problems)
    assert any("at least 2" in p for p in courier.problems)
    assert any("below 256" in p for p in courier.problems)
