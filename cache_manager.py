"""
مدير التخزين المؤقت (Cache Manager)

يوفر نظام تخزين مؤقت في الذاكرة مع:
- انتهاء صلاحية تلقائي (TTL)
- عمليات آمنة للخيوط (Thread-safe)
- إحصائيات الاستخدام
"""

import time
import threading
from typing import Any, Optional
from collections import OrderedDict


class CacheEntry:
    """تمثيل Entry في الكاش"""
    
    def __init__(self, value: Any, ttl: int):
        self.value = value
        self.expires_at = time.time() + ttl if ttl > 0 else None
    
    def is_expired(self) -> bool:
        """التحقق من انتهاء الصلاحية"""
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at


class CacheManager:
    """
    مدير التخزين المؤقت
    
    Attributes:
        ttl: الوقت الافتراضي لانتهاء الصلاحية بالثواني
        max_size: الحد الأقصى لعدد العناصر في الكاش
    """
    
    def __init__(self, ttl: int = 300, max_size: int = 1000):
        """
        تهيئة مدير الكاش
        
        Args:
            ttl: وقت انتهاء الصلاحية بالثواني (افتراضي: 5 دقائق)
            max_size: الحد الأقصى للعناصر (افتراضي: 1000)
        """
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._default_ttl = ttl
        self._max_size = max_size
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
    
    def get(self, key: str) -> Optional[Any]:
        """
        استرجاع قيمة من الكاش
        
        Args:
            key: مفتاح القيمة
            
        Returns:
            القيمة المخزنة أو None إذا لم توجد أو انتهت صلاحيتها
        """
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None
            
            entry = self._cache[key]
            
            # التحقق من انتهاء الصلاحية
            if entry.is_expired():
                del self._cache[key]
                self._misses += 1
                return None
            
            # نقل المفتاح إلى النهاية (LRU)
            self._cache.move_to_end(key)
            self._hits += 1
            return entry.value
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """
        تخزين قيمة في الكاش
        
        Args:
            key: مفتاح القيمة
            value: القيمة المراد تخزينها
            ttl: وقت انتهاء الصلاحية بالثواني (اختياري)
        """
        with self._lock:
            # استخدام TTL المخصص أو الافتراضي
            entry_ttl = ttl if ttl is not None else self._default_ttl
            
            # إذا كان المفتاح موجوداً، نحذفه أولاً لإعادة الترتيب
            if key in self._cache:
                del self._cache[key]
            
            # التحقق من حجم الكاش
            while len(self._cache) >= self._max_size:
                # حذف أقدم عنصر (أول عنصر)
                self._cache.popitem(last=False)
            
            # إضافة العنصر الجديد
            self._cache[key] = CacheEntry(value, entry_ttl)
    
    def delete(self, key: str) -> bool:
        """
        حذف قيمة من الكاش
        
        Args:
            key: مفتاح القيمة
            
        Returns:
            True إذا تم الحذف، False إذا لم يكن المفتاح موجوداً
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False
    
    def clear(self) -> None:
        """مسح كل عناصر الكاش"""
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0
    
    def cleanup_expired(self) -> int:
        """
        تنظيف العناصر منتهية الصلاحية
        
        Returns:
            عدد العناصر التي تم حذفها
        """
        with self._lock:
            expired_keys = [
                key for key, entry in self._cache.items()
                if entry.is_expired()
            ]
            
            for key in expired_keys:
                del self._cache[key]
            
            return len(expired_keys)
    
    def get_stats(self) -> dict:
        """
        الحصول على إحصائيات الكاش
        
        Returns:
            قاموس يحتوي على الإحصائيات
        """
        with self._lock:
            total_requests = self._hits + self._misses
            hit_rate = (self._hits / total_requests * 100) if total_requests > 0 else 0
            
            return {
                'size': len(self._cache),
                'max_size': self._max_size,
                'hits': self._hits,
                'misses': self._misses,
                'hit_rate': round(hit_rate, 2),
                'expired_count': sum(1 for e in self._cache.values() if e.is_expired())
            }
    
    def contains(self, key: str) -> bool:
        """
        التحقق من وجود مفتاح في الكاش
        
        Args:
            key: المفتاح
            
        Returns:
            True إذا كان المفتاح موجوداً وصالحة، False otherwise
        """
        return self.get(key) is not None
    
    def keys(self) -> list:
        """
        الحصول على جميع المفاتيح الصالحة
        
        Returns:
            قائمة بالمفاتيح
        """
        with self._lock:
            return [
                key for key, entry in self._cache.items()
                if not entry.is_expired()
            ]


# كاش عام للاستخدام في البوت
_global_cache: Optional[CacheManager] = None


def get_cache(ttl: int = 300) -> CacheManager:
    """
    الحصول على مثيل كاش عام
    
    Args:
        ttl: وقت انتهاء الصلاحية بالثواني
        
    Returns:
        مثيل CacheManager
    """
    global _global_cache
    if _global_cache is None:
        _global_cache = CacheManager(ttl=ttl)
    return _global_cache
