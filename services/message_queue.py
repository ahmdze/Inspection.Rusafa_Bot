"""
Message Queue Service
=====================
Redis-based message queue for async task processing and multi-instance support.
Provides pub/sub messaging and task queuing capabilities.
"""

import asyncio
import json
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)


class MessageQueueService:
    """
    Redis-based message queue service.
    
    Features:
    - Pub/Sub for real-time notifications
    - Task queues for background processing
    - Multi-instance coordination
    - Automatic reconnection
    """
    
    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis_url = redis_url
        self._redis = None
        self._pubsub = None
        self._connected = False
        self._reconnect_delay = 5
        self._max_reconnect_attempts = 10
    
    async def connect(self) -> bool:
        """Connect to Redis"""
        try:
            import redis.asyncio as redis
            
            self._redis = redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=10,
                socket_keepalive=True
            )
            
            # Test connection
            await self._redis.ping()
            self._connected = True
            logger.info("✅ Connected to Redis message queue")
            return True
            
        except ImportError:
            logger.warning("Redis library not installed, message queue disabled")
            return False
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            self._connected = False
            return False
    
    async def disconnect(self):
        """Disconnect from Redis"""
        if self._pubsub:
            await self._pubsub.close()
            self._pubsub = None
        
        if self._redis:
            await self._redis.close()
            self._redis = None
        
        self._connected = False
        logger.info("Disconnected from Redis")
    
    async def _ensure_connected(self) -> bool:
        """Ensure connection is active, reconnect if needed"""
        if not self._connected or not self._redis:
            return await self.connect()
        
        try:
            await self._redis.ping()
            return True
        except Exception:
            logger.warning("Redis connection lost, attempting reconnect...")
            return await self.connect()
    
    # ========== Publishing Messages ==========
    
    async def publish(self, channel: str, data: Dict[str, Any]) -> bool:
        """Publish a message to a channel"""
        if not await self._ensure_connected():
            return False
        
        try:
            message = json.dumps({
                'data': data,
                'timestamp': datetime.utcnow().isoformat()
            }, ensure_ascii=False)
            
            await self._redis.publish(channel, message)
            return True
        except Exception as e:
            logger.error(f"Failed to publish to {channel}: {e}")
            return False
    
    async def publish_notification(self, admin_id: int, message: str) -> bool:
        """Publish a notification message"""
        return await self.publish('notifications', {
            'type': 'admin_notification',
            'admin_id': admin_id,
            'message': message
        })
    
    # ========== Consuming Messages ==========
    
    async def subscribe(self, channel: str) -> bool:
        """Subscribe to a channel"""
        if not await self._ensure_connected():
            return False
        
        try:
            self._pubsub = self._redis.pubsub()
            await self._pubsub.subscribe(channel)
            logger.info(f"Subscribed to channel: {channel}")
            return True
        except Exception as e:
            logger.error(f"Failed to subscribe to {channel}: {e}")
            return False
    
    async def consume(self, channel: str, timeout: float = 1.0) -> Optional[Dict]:
        """Consume a single message from a channel"""
        if not self._pubsub:
            if not await self.subscribe(channel):
                return None
        
        try:
            message = await asyncio.wait_for(
                self._pubsub.get_message(ignore_subscribe_messages=True),
                timeout=timeout
            )
            
            if message and message['type'] == 'message':
                return json.loads(message['data'])
            return None
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            logger.error(f"Error consuming message: {e}")
            return None
    
    async def consume_batch(self, channel: str, batch_size: int = 10, 
                           timeout: float = 1.0) -> List[Dict]:
        """Consume multiple messages from a channel"""
        messages = []
        
        for _ in range(batch_size):
            msg = await self.consume(channel, timeout=timeout)
            if msg:
                messages.append(msg)
            else:
                break
        
        return messages
    
    # ========== Task Queue Operations ==========
    
    async def enqueue_task(self, queue_name: str, task_data: Dict[str, Any]) -> bool:
        """Add a task to a queue"""
        if not await self._ensure_connected():
            return False
        
        try:
            task = json.dumps({
                'task': task_data,
                'enqueued_at': datetime.utcnow().isoformat()
            }, ensure_ascii=False)
            
            await self._redis.lpush(queue_name, task)
            return True
        except Exception as e:
            logger.error(f"Failed to enqueue task: {e}")
            return False
    
    async def dequeue_task(self, queue_name: str, timeout: float = 1.0) -> Optional[Dict]:
        """Get a task from a queue"""
        if not await self._ensure_connected():
            return None
        
        try:
            result = await self._redis.brpop(queue_name, timeout=int(timeout))
            
            if result:
                _, task_json = result
                return json.loads(task_json)
            return None
        except Exception as e:
            logger.error(f"Failed to dequeue task: {e}")
            return None
    
    async def get_queue_length(self, queue_name: str) -> int:
        """Get the number of tasks in a queue"""
        if not await self._ensure_connected():
            return 0
        
        try:
            return await self._redis.llen(queue_name)
        except Exception:
            return 0
    
    # ========== Multi-Instance Coordination ==========
    
    async def acquire_lock(self, lock_name: str, ttl: int = 30) -> bool:
        """Acquire a distributed lock"""
        if not await self._ensure_connected():
            return False
        
        try:
            lock_key = f"lock:{lock_name}"
            # Set with NX (only if not exists) and EX (expiration)
            result = await self._redis.set(lock_key, "1", nx=True, ex=ttl)
            return result is not None
        except Exception as e:
            logger.error(f"Failed to acquire lock {lock_name}: {e}")
            return False
    
    async def release_lock(self, lock_name: str) -> bool:
        """Release a distributed lock"""
        if not await self._ensure_connected():
            return False
        
        try:
            lock_key = f"lock:{lock_name}"
            return await self._redis.delete(lock_key) > 0
        except Exception as e:
            logger.error(f"Failed to release lock {lock_name}: {e}")
            return False
    
    async def is_leader(self, instance_id: str, leader_key: str = "bot_leader") -> bool:
        """Check if this instance is the leader (for single-instance tasks)"""
        if not await self._ensure_connected():
            return True  # Assume leader if no Redis
        
        try:
            leader = await self._redis.get(leader_key)
            
            if leader is None:
                # Try to become leader
                acquired = await self.acquire_lock(leader_key, ttl=60)
                if acquired:
                    await self._redis.set(leader_key, instance_id, ex=60)
                    return True
                return False
            
            return leader == instance_id
        except Exception as e:
            logger.error(f"Leader check failed: {e}")
            return True  # Assume leader on error to avoid losing functionality
    
    # ========== Health Check ==========
    
    async def health_check(self) -> Dict[str, Any]:
        """Check message queue health"""
        status = {
            'connected': self._connected,
            'latency_ms': None,
            'error': None
        }
        
        if not await self._ensure_connected():
            status['error'] = 'Not connected'
            return status
        
        try:
            start = datetime.utcnow()
            await self._redis.ping()
            end = datetime.utcnow()
            status['latency_ms'] = (end - start).total_seconds() * 1000
        except Exception as e:
            status['error'] = str(e)
            status['connected'] = False
        
        return status


# Singleton instance
_message_queue_instance: Optional[MessageQueueService] = None


def get_message_queue(redis_url: str = None) -> MessageQueueService:
    """Get or create message queue instance"""
    global _message_queue_instance
    
    if _message_queue_instance is None:
        url = redis_url or "redis://localhost:6379"
        _message_queue_instance = MessageQueueService(url)
    
    return _message_queue_instance


async def init_message_queue(redis_url: str = None) -> MessageQueueService:
    """Initialize and connect message queue"""
    mq = get_message_queue(redis_url)
    await mq.connect()
    return mq
