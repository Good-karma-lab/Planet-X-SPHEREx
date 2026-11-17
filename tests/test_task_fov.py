"""A finding task opens at a field that holds the whole track (service/routes_tasks.task_fov): 10′, else 20′ or 60′."""

from __future__ import annotations

from service.routes_tasks import task_fov


def track(span_arcmin: float) -> dict[str, object]:
    return {"finding_id": "mover:abc", "kind": "mover", "data": {"track": {"ra": [100.0, 100.0 + span_arcmin / 60.0], "dec": [0.0, 0.0]}}}


def test_the_field_holds_the_whole_track() -> None:
    assert task_fov(None) == 10.0 and task_fov(track(1.0)) == 10.0
    assert task_fov(track(7.0)) == 20.0  # 7′ × 1.25 + 2′ = 10.75′: does not fit in 10′
    assert task_fov(track(12.0)) == 20.0 and task_fov(track(20.0)) == 60.0
    film = {"finding_id": "film:2e26f8ee6415", "kind": "film", "data": {"field_params": {"ra": 1, "dec": 2, "fov": 20}}}
    assert task_fov(film) == 20.0 and task_fov({"finding_id": "film:x", "data": {}}) == 60.0
