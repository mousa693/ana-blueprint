"""ANA command-line interface.

    ana-run demo            run the built-in lighthouse journey (deterministic)
    ana-run play            interactive: you are the protagonist
    ana-run auto --cycles N an autonomous journey from a seeded identity
    ana-run doctor          environment check (model availability, cores)
"""
from __future__ import annotations

import typer
from rich.console import Console

from .config import ANAConfig
from .observers import ConsoleObserver, JSONLObserver
from .session import build_session
from .engine.drivers import HumanDriver, ScriptedDriver
from .identity.core import IdentityCore
from .identity.memory import ShardMemory

app = typer.Typer(help="ANA — Adaptive Neural-Simulation Architecture", no_args_is_help=True)
console = Console()


def _seed_lighthouse_identity() -> IdentityCore:
    """The blueprint's worked example: insecure-about-leadership, values honesty,
    a childhood lighthouse memory, a goal to find purpose."""
    ic = IdentityCore(name="the wanderer", memory=ShardMemory(db_path=None))
    ic.set_trait("leadership_confidence", 0.25)
    ic.set_trait("honesty", 0.8)
    ic.set_trait("courage", 0.4)
    ic.add_belief("I am not the kind of person others follow.")
    ic.add_goal("find my purpose", horizon="life", is_future_memory=True)
    ic.add_goal("become someone who can lead", horizon="mid", is_future_memory=True)
    ic.seed_memory("A childhood by a lighthouse on a grey coast.",
                   themes=["purpose", "courage"], symbols=["lighthouse"])
    ic.active_themes = ["leadership", "purpose", "courage"]
    ic.emotion.valence = -0.1
    ic.emotion.arousal = 0.45
    ic.emotion.dominant = ic.emotion.dominant.CURIOSITY
    return ic


@app.command()
def demo(cycles: int = 8, log: str = ""):
    """Run the deterministic lighthouse journey (no API key required)."""
    cfg = ANAConfig(use_llm=False, max_cycles=cycles)
    observers = [ConsoleObserver()]
    if log:
        observers.append(JSONLObserver(log))
    sess = build_session(cfg, identity=_seed_lighthouse_identity(), observers=observers,
                         driver=ScriptedDriver([
                             "I explore the town and talk to the fishermen.",
                             "I climb toward the lighthouse to see what's wrong.",
                             "I organize a rescue and signal the stranded ship.",
                             "I speak honestly to the council about what happened.",
                             "I accept the role they offer, uneasy but willing.",
                         ]))
    sess.run()
    _summary(sess)
    sess.close()


@app.command()
def auto(cycles: int = 10, seed: int = 42, use_llm: bool = False, log: str = ""):
    """An autonomous journey: the self acts for itself each cycle."""
    cfg = ANAConfig(use_llm=use_llm, max_cycles=cycles, seed=seed)
    observers = [ConsoleObserver()]
    if log:
        observers.append(JSONLObserver(log))
    sess = build_session(cfg, identity=_seed_lighthouse_identity(), observers=observers)
    sess.run()
    _summary(sess)
    sess.close()


@app.command()
def play(cycles: int = 8, use_llm: bool = True):
    """Interactive journey — you type the protagonist's actions."""
    cfg = ANAConfig(use_llm=use_llm, max_cycles=cycles)
    sess = build_session(cfg, identity=_seed_lighthouse_identity(),
                         observers=[ConsoleObserver(show_telemetry=False)],
                         driver=HumanDriver())
    sess.run()
    _summary(sess)
    sess.close()


@app.command()
def doctor():
    """Check the environment: cores, model availability, dependencies."""
    from .llm.gateway import ModelGateway
    from .engine.convergence import ConvergenceEngine

    console.print("[bold]ANA environment check[/bold]")
    ok = True
    try:
        ConvergenceEngine(seed=1).step(0.2)
        console.print("  pi-phi convergence core: [green]ok[/]")
    except Exception as e:  # pragma: no cover
        console.print(f"  pi-phi core: [red]FAIL[/] {e}"); ok = False
    try:
        ShardMemory(db_path=None).close()
        console.print("  MCMG memory core: [green]ok[/]")
    except Exception as e:  # pragma: no cover
        console.print(f"  MCMG core: [red]FAIL[/] {e}"); ok = False
    gw = ModelGateway()
    console.print(f"  model gateway: {'[green]online[/] (' + gw.model + ')' if gw.available else '[yellow]offline[/] (deterministic mode)'}")
    console.print("[green]ready[/]" if ok else "[red]problems found[/]")


def _summary(sess) -> None:
    ident = sess.identity
    console.print(f"\n{'═' * 70}")
    console.print("[bold]Journey complete[/bold]")
    console.print(f"  final convergence: {ident.convergence:.2f}")
    console.print(f"  traits:")
    for name, t in sorted(ident.traits.items()):
        console.print(f"    {name}: {t.value:.2f}")
    console.print(f"  memory shards: {ident.memory.stats().get('total_nodes')}")
    console.print(f"  goals:")
    for g in ident.goals:
        console.print(f"    [{g.progress:.0%}] {g.description}")


if __name__ == "__main__":
    app()
