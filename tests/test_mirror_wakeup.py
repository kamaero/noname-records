from types import SimpleNamespace

import pytest

from app.services import mirror_wakeup as wake
from app.workers import mirror


SETTINGS = SimpleNamespace(audio_nas_path="/separate/audio", audio_storage_path="/local/audio", redis_url="redis://unused.invalid/0", mirror_interval_minutes=5)


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []
        self.on_sleep = None

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep:
            self.on_sleep()


class Redis:
    def __init__(self, clock):
        self.clock = clock
        self.values = {}
        self.close_count = 0
        self.eval_count = 0

    def set(self, key, value, *, ex):
        self.values[key] = (value, self.clock.now + ex)
        return True

    def eval(self, script, numkeys, key):
        assert script == wake._CONSUME and numkeys == 1
        self.eval_count += 1
        stored = self.values.pop(key, None)
        if stored is not None and stored[1] > self.clock.now:
            return stored[0]

    def close(self):
        self.close_count += 1


def wait(clock, redis, seconds=300):
    return wake.wait_for_mirror_wakeup(seconds, settings=SETTINGS, redis_factory=lambda settings: redis,
        sleep=clock.sleep, monotonic=clock.monotonic)


def test_one_thousand_signals_coalesce_into_one_wakeup_with_a_ttl():
    clock = Clock()
    redis = Redis(clock)
    for _ in range(1000):
        assert wake.request_mirror_wakeup(settings=SETTINGS, redis_factory=lambda settings: redis)
    assert len(redis.values) == 1
    assert next(iter(redis.values.values())) == ("1", 600)
    assert wait(clock, redis) is True
    assert clock.now == 1 and redis.values == {}
    assert wait(clock, redis) is False
    assert clock.now == 301


def test_signal_during_wait_interrupts_sleep_within_poll_interval():
    clock = Clock()
    redis = Redis(clock)
    def new_audio():
        if clock.now == 3:
            wake.request_mirror_wakeup(settings=SETTINGS, redis_factory=lambda settings: redis)
    clock.on_sleep = new_audio
    assert wait(clock, redis) is True
    assert clock.now == 3


def test_continuous_signals_keep_minimum_gap_between_passes():
    clock = Clock()
    redis = Redis(clock)
    clock.on_sleep = lambda: wake.request_mirror_wakeup(settings=SETTINGS, redis_factory=lambda settings: redis)
    for _ in range(10):
        assert wait(clock, redis) is True
    assert clock.now == 10
    assert clock.sleeps == [1.0] * 10


def test_no_signal_preserves_the_full_period_and_closes_client():
    clock = Clock()
    redis = Redis(clock)
    assert wait(clock, redis) is False
    assert clock.now == 300
    assert redis.close_count == 1


def test_expired_signal_does_not_wake_a_later_loop():
    clock = Clock()
    redis = Redis(clock)
    wake.request_mirror_wakeup(settings=SETTINGS, redis_factory=lambda settings: redis)
    clock.now = 601
    assert wait(clock, redis) is False
    assert clock.now == 901 and redis.values == {}


@pytest.mark.parametrize("failure", [ImportError("redis absent"), ConnectionError("private configuration")])
def test_missing_redis_keeps_original_period_without_raising(failure, caplog):
    clock = Clock()
    def unavailable(settings):
        clock.now += 0.2
        raise failure
    caplog.set_level("DEBUG")
    assert wake.request_mirror_wakeup(settings=SETTINGS, redis_factory=unavailable) is False
    clock.now = 0
    assert wake.wait_for_mirror_wakeup(300, settings=SETTINGS, redis_factory=unavailable,
        sleep=clock.sleep, monotonic=clock.monotonic) is False
    assert clock.now == pytest.approx(300)
    assert "private configuration" not in caplog.text


def test_redis_failure_mid_wait_sleeps_only_remaining_period():
    clock = Clock()
    redis = Redis(clock)
    original = redis.eval
    def fail_after_two_checks(*args):
        if redis.eval_count >= 2:
            raise ConnectionError("private")
        return original(*args)
    redis.eval = fail_after_two_checks
    assert wait(clock, redis) is False
    assert clock.now == 300
    assert clock.sleeps[-1] == 297
    assert redis.close_count == 1


def test_separate_storage_roots_do_not_wake_each_other():
    clock = Clock()
    redis = Redis(clock)
    other = SimpleNamespace(audio_nas_path="/separate/audio", audio_storage_path="/other/audio")
    wake.request_mirror_wakeup(settings=other, redis_factory=lambda settings: redis)
    assert wait(clock, redis, seconds=5) is False
    assert len(redis.values) == 1


def test_equivalent_storage_paths_share_one_signal_key(tmp_path, monkeypatch):
    storage = tmp_path / "audio"
    storage.mkdir()
    alias = tmp_path / "audio-link"
    alias.symlink_to(storage, target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    absolute = SimpleNamespace(audio_nas_path="/separate/audio", audio_storage_path=str(storage))
    relative = SimpleNamespace(audio_nas_path="/separate/audio", audio_storage_path="./audio/../audio")
    symlinked = SimpleNamespace(audio_nas_path="/separate/audio", audio_storage_path=str(alias))
    assert wake._key(absolute) == wake._key(relative) == wake._key(symlinked)


def test_databases_sharing_storage_and_redis_are_separate_installations(caplog):
    clock = Clock()
    redis = Redis(clock)
    first = SimpleNamespace(audio_nas_path="/separate/audio", audio_storage_path="/local/audio", database_url="postgres://user:first-secret@db/first")
    second = SimpleNamespace(audio_nas_path="/separate/audio", audio_storage_path="/local/audio", database_url="postgres://user:second-secret@db/second")
    assert wake._key(first) != wake._key(second)
    assert "secret" not in wake._key(first)
    assert "postgres" not in wake._key(first)
    wake.request_mirror_wakeup(settings=first, redis_factory=lambda settings: redis)
    assert wake.wait_for_mirror_wakeup(5, settings=second, redis_factory=lambda settings: redis,
        sleep=clock.sleep, monotonic=clock.monotonic) is False
    assert len(redis.values) == 1
    assert "secret" not in caplog.text


@pytest.mark.parametrize("query", ["", "?socket_timeout=45&socket_connect_timeout=40&retry_on_timeout=True"])
def test_effective_redis_connection_keeps_short_timeouts_despite_url_overrides(query, monkeypatch, caplog):
    from redis.connection import Connection
    def no_network(self):
        pytest.fail("тест попытался подключиться к Redis")
    monkeypatch.setattr(Connection, "connect", no_network)
    settings = SimpleNamespace(audio_nas_path="/separate/audio", audio_storage_path="/local/audio",
        redis_url="redis://user:private-password@unused.invalid/0" + query)
    client = wake._client(settings)
    # make_connection создаёт объект без connect: проверяем итоговый pool,
    # а не kwargs вызова from_url, которые query мог бы перезаписать.
    connection = client.connection_pool.make_connection()
    assert connection.socket_timeout == 0.2
    assert connection.socket_connect_timeout == 0.2
    assert connection.retry_on_timeout is False
    assert connection.retry_on_error == [] and connection.retry._retries == 0
    def failed_set(*args, **kwargs):
        raise ConnectionError("redis://user:private-password@unused.invalid")
    monkeypatch.setattr(client, "set", failed_set)
    caplog.set_level("DEBUG")
    assert wake.request_mirror_wakeup(settings=settings, redis_factory=lambda settings: client) is False
    assert "private-password" not in caplog.text and "unused.invalid" not in caplog.text


def test_existing_loop_is_the_only_copier_and_waits_between_full_passes(monkeypatch):
    events = []
    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def commit(self):
            events.append("commit")
    def copy(db):
        events.append("copy")
        return {"mirrored": 0, "failed": 0, "left": 0}
    def purge(db):
        events.append("purge")
        return {"purged": 0, "missing": 0, "reused": 0}
    def archive(db):
        events.append("archive")
        return {"archived": 0}
    waits = []
    class StopLoop(BaseException):
        pass
    def waiting(seconds, *, settings):
        waits.append(seconds)
        events.append("wait")
        assert settings is SETTINGS
        if len(waits) == 2:
            raise StopLoop
        return True
    monkeypatch.setattr(mirror, "mirror_once", copy)
    monkeypatch.setattr(mirror, "purge_deleted_once", purge)
    monkeypatch.setattr(mirror, "archive_pending_chapter_sessions", archive)
    monkeypatch.setattr(mirror, "heartbeat_background_run", lambda *a, **kw: None)
    monkeypatch.setattr(mirror, "wait_for_mirror_wakeup", waiting)
    with pytest.raises(StopLoop):
        mirror.mirror_loop(settings=SETTINGS, session_factory=Session, background_run_cls=object)
    assert events == ["copy", "commit", "purge", "archive", "wait"] * 2
    assert waits == [300, 300]


@pytest.mark.parametrize("mode,nas", [("one", "/separate/audio"), ("one", ""), ("studio", "")])
def test_one_seat_and_disabled_nas_never_open_redis_and_preserve_interval(mode, nas):
    clock = Clock()
    settings = SimpleNamespace(seat_mode=mode, audio_nas_path=nas, audio_storage_path="/local/audio")
    def forbidden(_):
        pytest.fail("установка не должна обращаться к Redis для сигнала зеркала")
    assert wake.request_mirror_wakeup(settings=settings, redis_factory=forbidden) is False
    assert wake.wait_for_mirror_wakeup(300, settings=settings, redis_factory=forbidden,
        sleep=clock.sleep, monotonic=clock.monotonic) is False
    assert clock.now == 300 and clock.sleeps == [300]
