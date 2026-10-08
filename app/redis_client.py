import redis
from rq import Queue
from app.config import settings

# Initialize Redis connection
redis_conn = redis.from_url(settings.redis_url)

# Initialize queues
# 'high' for urgent tasks (like user-initiated actions)
# 'default' for regular pipeline tasks
# 'low' for maintenance tasks
queue_high = Queue("high", connection=redis_conn)
queue_default = Queue("default", connection=redis_conn)
queue_low = Queue("low", connection=redis_conn)
# 'consilium' — консилиум, звуковая разметка и эмбиент: их читает отдельный воркер
# (RQ_QUEUES=consilium), чтобы прогон по книге на часы не держал сверку дублей. До 08.10
# этой ветки не было, и такие прогоны молча уходили в default.
queue_consilium = Queue("consilium", connection=redis_conn)

def get_queue(priority: str = "default"):
    # в режиме «одно место» Redis нет — задачи идут в поток внутри процесса
    from app.seat import one_seat
    if one_seat():
        from app.workers.local_queue import local_queue
        return local_queue(priority)
    if priority == "high":
        return queue_high
    elif priority == "low":
        return queue_low
    elif priority == "consilium":
        return queue_consilium
    return queue_default
