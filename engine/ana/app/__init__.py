"""ANA's public multi-user web product (docs/PRODUCT_SPEC.md §3).

``from ana.app.main import create_app`` builds the FastAPI app;
``uvicorn ana.app.main:app`` / ``python -m ana.app`` run it.
"""

__all__ = ["create_app"]


def __getattr__(name: str):
    if name == "create_app":
        from .main import create_app
        return create_app
    raise AttributeError(name)
