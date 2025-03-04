import json
import redis
import uuid
from urllib.parse import urlparse

def parse_redis_sentinel_url(redis_url):
    parsed_url = urlparse(redis_url)
    if parsed_url.scheme != "redis":
        raise ValueError("Invalid Redis URL scheme. Must be 'redis'.")

    return {
        "username": parsed_url.username or None,
        "password": parsed_url.password or None,
        "service": parsed_url.hostname or 'mymaster',
        "port": parsed_url.port or 6379,
        "db": int(parsed_url.path.lstrip("/") or 0),
    }

def get_redis_connection(redis_url, sentinels, decode_responses=True):
    """
    Creates a Redis connection from either a standard Redis URL or uses special
    parsing to setup a Sentinel connection, if given an array of host/port tuples.
    """
    if sentinels:
        redis_config = parse_redis_sentinel_url(redis_url)
        sentinel = redis.sentinel.Sentinel(
            sentinels,
            port=redis_config['port'],
            db=redis_config['db'],
            username=redis_config['username'],
            password=redis_config['password'],
            decode_responses=decode_responses
        )

        # Get a master connection from Sentinel
        return sentinel.master_for(redis_config['service'])
    else:
        # Standard Redis connection
        return redis.Redis.from_url(redis_url, decode_responses=decode_responses)

class RedisLock:
    def __init__(self, redis_url, lock_name, timeout_secs, sentinels=[]):
        self.lock_name = lock_name
        self.lock_id = str(uuid.uuid4())
        self.timeout_secs = timeout_secs
        self.lock_obtained = False
        self.redis = get_redis_connection(redis_url, sentinels, decode_responses=True)

    def aquire_lock(self):
        # nx=True will only set this key if it _hasn't_ already been set
        self.lock_obtained = self.redis.set(
            self.lock_name, self.lock_id, nx=True, ex=self.timeout_secs
        )
        return self.lock_obtained

    def renew_lock(self):
        # xx=True will only set this key if it _has_ already been set
        return self.redis.set(
            self.lock_name, self.lock_id, xx=True, ex=self.timeout_secs
        )

    def release_lock(self):
        lock_value = self.redis.get(self.lock_name)
        if lock_value and lock_value == self.lock_id:
            self.redis.delete(self.lock_name)


class RedisDict:
    def __init__(self, name, redis_url, sentinels=[]):
        self.name = name
        self.redis = get_redis_connection(redis_url, sentinels, decode_responses=True)

    def __setitem__(self, key, value):
        serialized_value = json.dumps(value)
        self.redis.hset(self.name, key, serialized_value)

    def __getitem__(self, key):
        value = self.redis.hget(self.name, key)
        if value is None:
            raise KeyError(key)
        return json.loads(value)

    def __delitem__(self, key):
        result = self.redis.hdel(self.name, key)
        if result == 0:
            raise KeyError(key)

    def __contains__(self, key):
        return self.redis.hexists(self.name, key)

    def __len__(self):
        return self.redis.hlen(self.name)

    def keys(self):
        return self.redis.hkeys(self.name)

    def values(self):
        return [json.loads(v) for v in self.redis.hvals(self.name)]

    def items(self):
        return [(k, json.loads(v)) for k, v in self.redis.hgetall(self.name).items()]

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def clear(self):
        self.redis.delete(self.name)

    def update(self, other=None, **kwargs):
        if other is not None:
            for k, v in other.items() if hasattr(other, "items") else other:
                self[k] = v
        for k, v in kwargs.items():
            self[k] = v

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]
