import redis
from env_variables import setting

pool = redis.ConnectionPool(
    host=setting.redis_host, port=setting.redis_port, db=0, decode_responses=True
)


def get_redis():
    return redis.Redis(connection_pool=pool)
