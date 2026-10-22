from ana.gateway import Gateway
from ana.world.town import ACTIONS_BY_KIND, TICKS_PER_HOUR
from ana.world.world import Option, World, in_hours

import pytest


def at(world: World, day: int, hour: int) -> World:
    world.tick = (day - 1) * 24 * TICKS_PER_HOUR + hour * TICKS_PER_HOUR
    return world


def test_hours_wrap_midnight():
    assert in_hours(23, (21, 10)) and in_hours(3, (21, 10)) and not in_hours(12, (21, 10))
    assert in_hours(9, (8, 17)) and not in_hours(17, (8, 17))


def test_options_follow_place_and_hours():
    w = at(World(), 1, 3)
    kinds = {o.spec.kind for o in w.options()}
    assert "sleep" in kinds and "work" not in kinds
    w.body.place = "boatyard"
    assert "work" not in {o.spec.kind for o in w.options()}  # closed at 3am
    at(w, 1, 10)
    assert "work" in {o.spec.kind for o in w.options()}
    # Every other place is one journey away, open or not.
    assert {o.move_to for o in w.options() if o.move_to} == {"home", "cafe", "market", "library", "harbor", "lighthouse", "road"}


def test_social_options_need_someone_present():
    w = at(World(), 1, 10)
    w.body.place = "boatyard"
    targets = {o.target for o in w.options() if o.spec.social}
    assert targets == {"ivo"}


def test_harmonizer_says_no():
    w = at(World(), 1, 10)
    w.body.place = "boatyard"
    work = Option(ACTIONS_BY_KIND["work"])
    assert not w.attempt(work, energy=0.1).ok  # too exhausted
    w.work_blocked_until = 1
    assert "chained" in w.attempt(work, energy=0.9).text
    w.body.place, w.body.money = "cafe", 1
    assert not w.attempt(Option(ACTIONS_BY_KIND["coffee"]), energy=0.9).ok


def test_work_pays_and_ivo_notices():
    w = at(World(), 1, 10)
    w.body.place = "boatyard"
    before, aff = w.body.money, w.npcs["ivo"].affection
    out = w.attempt(Option(ACTIONS_BY_KIND["work"]), energy=0.9)
    assert out.ok and w.body.money == before + 48 and w.npcs["ivo"].affection > aff


def test_npcs_remember_how_it_went():
    w = at(World(seed=3), 1, 12)
    w.body.place = "cafe"
    talk = Option(ACTIONS_BY_KIND["talk"], target="mara")
    results = [w.attempt(talk, energy=0.9).social for _ in range(30)]
    assert {"warm", "brief"} & set(results)
    assert w.npcs["mara"].talks == 30


def test_perception_is_local_and_never_replays():
    w = World()
    w.emit("weather", "rain", "world", None)
    w.emit("x", "at the harbor", "world", "harbor")
    w.emit("y", "at home", "world", "home")
    w.emit("action", "my own action", "twin", "home")
    seen = [e.text for e in w.perceive()]
    assert seen == ["rain", "at home"]
    assert w.perceive() == []


def test_gateway_limits_circumstances():
    w = World()
    g = Gateway(w, "Sam")
    g.circumstance("notice_sailing")
    with pytest.raises(RuntimeError):
        g.circumstance("found_book")
    with pytest.raises(ValueError):
        g.message("hi", suggest="fly")


def test_circumstance_adds_and_expires_an_opportunity():
    w = at(World(), 1, 10)
    Gateway(w, "Sam").circumstance("notice_sailing")
    w.body.place = "harbor"
    assert "sailing" in {o.spec.kind for o in w.options()}
    for _ in range(8 * 24 * TICKS_PER_HOUR):
        w.advance()
    at(w, w.day, 10)
    assert "sailing" not in {o.spec.kind for o in w.options()}
