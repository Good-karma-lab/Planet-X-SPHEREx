"""A request a few arcseconds off a field the service has is that field; farther off it is its own field."""

from service.workers import fieldjob


def test_a_place_near_a_known_field_snaps_to_it() -> None:
    fieldjob._CENTRES.clear()
    fieldjob.remember(331.09645, -56.80059, 20)
    p = fieldjob.canonical(331.077, -56.794, 20)  # the landing's link: 45″ off, within 1/20 of 20′ (60″)
    q = fieldjob.canonical(331.09645, -56.80059, 20)
    assert (p["ra"], p["dec"]) == (q["ra"], q["dec"])
    # at 5′ the tolerance is 15″: the same 45″ is another field
    fieldjob.remember(331.09645, -56.80059, 5)
    assert fieldjob.canonical(331.077, -56.794, 5)["ra"] != q["ra"]
    # another size never snaps to this one
    assert fieldjob.canonical(331.077, -56.794, 10)["ra"] != q["ra"]
    fieldjob._CENTRES.clear()
