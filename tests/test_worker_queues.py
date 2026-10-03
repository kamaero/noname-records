from app.worker import queues_from_env


def test_the_main_worker_keeps_its_queues_and_consilium_gets_its_own():
    assert queues_from_env(None) == ["high", "default", "low"]
    assert queues_from_env("") == ["high", "default", "low"]
    assert queues_from_env(" consilium ") == ["consilium"]
    assert queues_from_env("high, low") == ["high", "low"]
