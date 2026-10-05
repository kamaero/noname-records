"""Консилиум, звук и эмбиент зовут модель шага у её провайдера."""
from app.services import consilium_engine, step_models


def test_readers_come_from_the_steps(monkeypatch):
    models = {"consilium_reader_1": ("routerai", "a/one"), "consilium_reader_2": ("openrouter", "b/two")}
    monkeypatch.setattr(step_models, "step_model", lambda key: models[key])
    assert consilium_engine.readers() == (("opus", "routerai", "a/one"), ("sol", "openrouter", "b/two"))


def test_a_routed_ask_sends_each_model_to_its_provider(monkeypatch):
    sent = []
    monkeypatch.setattr("app.pipeline.llm_client._resolve_provider",
                        lambda provider: (f"key-{provider}", f"https://{provider}", "openai"))
    monkeypatch.setattr("app.pipeline.llm_client.call_chat",
                        lambda base, key, model, *a, **kw: sent.append((base, key, model)) or {"content": "{}"})
    ask = consilium_engine.routed_ask({"a/one": "routerai", "b/two": "openrouter"})
    ask("a/one", "s", "u", {"type": "object"})
    ask("b/two", "s", "u", {"type": "object"})
    assert sent == [("https://routerai", "key-routerai", "a/one"), ("https://openrouter", "key-openrouter", "b/two")]
