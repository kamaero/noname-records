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

def get_queue(priority: str = "default") -> Queue:
    if priority == "high":
        return queue_high
    elif priority == "low":
        return queue_low
    return queue_default
