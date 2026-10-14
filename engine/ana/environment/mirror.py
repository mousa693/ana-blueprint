"""Environment Mirror ("Myself") — the world that reflects the self.

Holds the world state and, crucially, the *reflectiveness dial*: early on the
world only loosely mirrors the identity; as convergence (from the pi-phi engine)
rises, the Self-Alignment Protocols turn the dial up and the world becomes a more
direct mirror. The archetypal mapping library translates identity themes into
world motifs (the blueprint's "abandonment -> empty houses" idea).
"""
from __future__ import annotations

from ..contracts import IdentitySnapshot, WorldState

# Theme -> symbolic motifs the world can surface to externalise it.
ARCHETYPAL_MAP: dict[str, list[str]] = {
    "leadership": ["an absent mayor", "a leaderless crowd", "a high vantage point"],
    "trust": ["a locked door with a offered key", "a stranger's outstretched hand"],
    "abandonment": ["empty houses", "a departing ship", "cold hearths"],
    "wisdom": ["a library", "an old sage", "carvings on stone"],
    "courage": ["a narrow bridge", "a guttering lighthouse", "a cliff edge"],
    "grief": ["falling leaves", "a shrouded statue", "rain that won't stop"],
    "freedom": ["open gates", "wide horizons", "unmoored boats"],
    "shame": ["mirrors turned to the wall", "averted gazes", "a hidden room"],
    "purpose": ["a distant lighthouse", "an unfinished map", "a calling bell"],
    "self": ["a still pool", "a double shadow", "a threshold"],
}

WEATHER_BY_VALENCE = [
    (-0.6, "storm"), (-0.2, "overcast"), (0.2, "clear"), (0.6, "golden light"),
]


# The journey's outer arc — places and hours advance as inner/outer converge, so
# the world visibly moves from an undefined edge toward a changed homecoming.
JOURNEY_PLACES = [
    "an unmarked threshold",
    "a town waking by the water",
    "the climbing road out of town",
    "a summit wrapped in storm",
    "a high ridge where the wind lives",
    "the long road home",
]
JOURNEY_HOURS = ["dawn", "morning", "midday", "late afternoon", "dusk", "first light"]
# The preposition each place takes in prose ("on the climbing road out of town").
PLACE_PREP: dict[str, str] = {
    "an unmarked threshold": "at",
    "a town waking by the water": "in",
    "the climbing road out of town": "on",
    "a summit wrapped in storm": "on",
    "a high ridge where the wind lives": "on",
    "the long road home": "on",
}
# Weather a place implies. It overrides the mirror's valence weather, so the
# storm-wrapped summit is never described as "clear".
PLACE_WEATHER: dict[str, str] = {
    "a summit wrapped in storm": "storm",
    "a high ridge where the wind lives": "clearing",
}


def place_prep(location: str) -> str:
    """The preposition for ``location`` in prose ("at" when unknown)."""
    return PLACE_PREP.get(location, "at")


# One place per Hero's-journey stage, in story order.
STAGE_ORDER = ["call", "threshold", "mentor", "ordeal", "reward", "return"]


class EnvironmentMirror:
    def __init__(self, world: WorldState | None = None, follow_journey: bool = True):
        self.world = world or WorldState()
        self.follow_journey = follow_journey

    def advance_place(self, convergence: float, stage: str | None = None) -> None:
        """Move the world's location/hour along the journey. When the arc names a
        Hero's-journey stage, the place follows the story; otherwise it follows
        convergence."""
        if not self.follow_journey:
            return
        if stage in STAGE_ORDER:
            idx = STAGE_ORDER.index(stage)
        else:
            idx = max(0, min(len(JOURNEY_PLACES) - 1,
                             int(max(0.0, min(0.999, convergence)) * len(JOURNEY_PLACES))))
        before = self.world.location
        self.world.location = JOURNEY_PLACES[idx]
        self.world.time_of_day = JOURNEY_HOURS[idx]
        if self.world.location in PLACE_WEATHER:
            self.world.weather = PLACE_WEATHER[self.world.location]
        elif before in PLACE_WEATHER and self.world.weather == PLACE_WEATHER[before]:
            # Leaving a place that brought its own weather: the sky clears
            # (reflect() re-colours it from the mood when the dial is up).
            self.world.weather = "clear"

    def reflect(self, identity: IdentitySnapshot, reflectiveness: float) -> WorldState:
        """Shape the world from the identity, scaled by the reflectiveness dial.

        At low reflectiveness the world stays subtle (few direct motifs, mood
        only loosely tied to emotion). At high reflectiveness it openly mirrors
        the self — stormy weather for a stormy mind, theme motifs made explicit.
        """
        w = self.world
        w.reflectiveness = max(0.0, min(1.0, reflectiveness))

        # Weather tracks emotional valence more strongly as the dial rises.
        if w.reflectiveness >= 0.4:
            w.weather = _weather_for(identity.emotion.valence)
        # ...but a place that carries its own weather keeps it.
        if w.location in PLACE_WEATHER:
            w.weather = PLACE_WEATHER[w.location]
        w.ambience = "charged" if identity.emotion.arousal > 0.6 else "quiet"

        # Surface motifs for active themes, more of them as the dial rises.
        budget = 1 + int(round(w.reflectiveness * 3))
        motifs: list[str] = []
        # Rotate through each theme's motif bank over time, so the world keeps
        # finding new images for the same inner theme instead of one fixed prop.
        themes = list(identity.active_themes)
        if themes:
            k = w.tick % len(themes)
            themes = themes[k:] + themes[:k]
        for theme in themes:
            bank = ARCHETYPAL_MAP.get(theme, [])
            if bank:
                j = w.tick % len(bank)
                bank = bank[j:] + bank[:j]
            for motif in bank:
                if motif not in motifs:
                    motifs.append(motif)
                if len(motifs) >= budget:
                    break
            if len(motifs) >= budget:
                break
        w.motifs = motifs
        return w

    def apply_world_change(self, *, location: str | None = None,
                           time_of_day: str | None = None) -> None:
        if location:
            self.world.location = location
        if time_of_day:
            self.world.time_of_day = time_of_day

    def advance(self) -> None:
        self.world.tick += 1


def _weather_for(valence: float) -> str:
    for threshold, weather in WEATHER_BY_VALENCE:
        if valence <= threshold:
            return weather
    return "golden light"
