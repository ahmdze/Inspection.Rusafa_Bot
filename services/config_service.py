"""
Configuration Service
=====================
Centralized configuration management with hot-reload support.
"""

import os
import hashlib
from typing import List, Optional
from dotenv import load_dotenv


class ConfigService:
    """
    Singleton configuration service.
    
    Provides centralized access to all configuration values with:
    - Hot-reload support (re-reads .env on demand)
    - Type validation
    - Default values
    """
    
    _instance = None
    
    def __init__(self):
        self._admin_ids: List[int] = []
        self._admin_ids_hash: str = ""
        self._token: str = ""
        self._database_url: str = ""
        self._postgres_config: dict = {}
        self.reload()
    
    @classmethod
    def get_instance(cls) -> 'ConfigService':
        """Get singleton instance"""
        if cls._instance is None:
            cls._instance = ConfigService()
        return cls._instance
    
    @classmethod
    def reset_instance(cls):
        """Reset singleton (useful for testing)"""
        cls._instance = None
    
    def reload(self):
        """Reload configuration from environment"""
        # Load .env file
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
        if os.path.exists(env_path):
            load_dotenv(env_path)
        
        # Load Telegram config
        self._token = os.getenv('TOKEN', '').strip() or os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
        
        admin_ids_raw = os.getenv('ADMIN_IDS', '').strip()
        self._admin_ids = [int(x.strip()) for x in admin_ids_raw.split(',') if x.strip().isdigit()]
        self._admin_ids_hash = hashlib.sha256(admin_ids_raw.encode()).hexdigest()
        
        # Load database config
        self._database_url = os.getenv('DATABASE_URL', '')
        
        # Detect database type
        if self._database_url.startswith('postgresql://'):
            # Parse PostgreSQL URL
            # Format: postgresql://user:password@host:port/database
            try:
                from urllib.parse import urlparse
                parsed = urlparse(self._database_url)
                self._postgres_config = {
                    'host': parsed.hostname or 'localhost',
                    'port': parsed.port or 5432,
                    'database': parsed.path.lstrip('/') or 'inspection_bot',
                    'user': parsed.username or 'postgres',
                    'password': parsed.password or '',
                }
            except Exception:
                self._postgres_config = {}
        else:
            # SQLite fallback
            self._postgres_config = {}
        
        # Load other config
        self._log_level = os.getenv('LOG_LEVEL', 'INFO')
        self._cache_ttl = int(os.getenv('CACHE_TTL', '300'))
        self._cache_max_size = int(os.getenv('CACHE_MAX_SIZE', '1000'))
    
    # ========== Getters ==========
    
    def get_token(self) -> str:
        """Get bot token"""
        return self._token
    
    def get_admin_ids(self) -> List[int]:
        """Get admin IDs list"""
        return self._admin_ids.copy()
    
    def get_admin_ids_hash(self) -> str:
        """Get hashed admin IDs"""
        return self._admin_ids_hash
    
    def is_admin(self, user_id: int) -> bool:
        """Check if user is admin"""
        return user_id in self._admin_ids
    
    def get_database_url(self) -> str:
        """Get database URL"""
        return self._database_url
    
    def get_postgres_config(self) -> dict:
        """Get PostgreSQL configuration"""
        return self._postgres_config.copy()
    
    def is_postgresql(self) -> bool:
        """Check if using PostgreSQL"""
        return bool(self._postgres_config)
    
    def get_log_level(self) -> str:
        """Get logging level"""
        return self._log_level
    
    def get_cache_ttl(self) -> int:
        """Get cache TTL in seconds"""
        return self._cache_ttl
    
    def get_cache_max_size(self) -> int:
        """Get cache max size"""
        return self._cache_max_size
    
    def get_all_config(self) -> dict:
        """Get all configuration as dictionary"""
        return {
            'token': self._token,
            'admin_ids': self._admin_ids.copy(),
            'admin_ids_hash': self._admin_ids_hash,
            'database_url': self._database_url,
            'postgres_config': self._postgres_config.copy(),
            'is_postgresql': self.is_postgresql(),
            'log_level': self._log_level,
            'cache_ttl': self._cache_ttl,
            'cache_max_size': self._cache_max_size,
        }


# Convenience functions
def get_config() -> ConfigService:
    """Get configuration service instance"""
    return ConfigService.get_instance()


def reload_config():
    """Reload configuration"""
    ConfigService.get_instance().reload()
