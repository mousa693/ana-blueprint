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
    "the high places where the wind lives",
    "the summit, with the storm all around",
    "the long road home, changed",
]
JOURNEY_HOURS = ["dawn", "morning", "midday", "late afternoon", "dusk", "first light"]


class EnvironmentMirror:
    def __init__(self, world: WorldState | None = None, follow_journey: bool = True):
        self.world = world or WorldState()
        self.follow_journey = follow_journey

    def advance_place(self, convergence: float) -> None:
        """Move the world's location/hour along the journey arc with convergence."""
        if not self.follow_journey:
            return
        idx = max(0, min(len(JOURNEY_PLACES) - 1,
                         int(max(0.0, min(0.999, convergence)) * len(JOURNEY_PLACES))))
        self.world.location = JOURNEY_PLACES[idx]
        self.world.time_of_day = JOURNEY_HOURS[idx]

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
        w.ambience = "charged" if identity.emotion.arousal > 0.6 else "quiet"

        # Surface motifs for active themes, more of them as the dial rises.
        budget = 1 + int(round(w.reflectiveness * 3))
        motifs: list[str] = []
        for theme in identity.active_themes:
            for motif in ARCHETYPAL_MAP.get(theme, []):
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
