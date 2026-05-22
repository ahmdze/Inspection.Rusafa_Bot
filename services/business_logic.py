"""
Business Logic Layer - منطق الأعمال
====================================
This module contains all business logic separated from the presentation layer.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, asdict
from contextlib import asynccontextmanager
from enum import Enum

logger = logging.getLogger(__name__)


class VisitStatus(str, Enum):
    OPEN = "مفتوحة"
    CLOSED = "مغلقة"


@dataclass
class VisitData:
    id: int
    institution_name: str
    visit_date: str
    status: str
    scheduled_date: Optional[str] = None
    manager_id: Optional[int] = None
    leader_id: Optional[int] = None


@dataclass  
class DraftData:
    id: int
    visit_id: int
    user_id: int
    state: int
    payload: Dict[str, Any]
    updated_at: str


class BusinessLogicService:
    """Main business logic service with dependency injection."""
    
    def __init__(self, db_pool, cache_manager, message_queue=None):
        self.db_pool = db_pool
        self.cache = cache_manager
        self.message_queue = message_queue
        self._lock = asyncio.Lock()
        
        # Cached static lists
        self._axes_list = None
        self._destinations_list = None
        self._section_presets = None
    
    async def initialize_static_cache(self):
        """Initialize cached static lists for performance."""
        if self._axes_list is None:
            self._axes_list = [
                ["المعلومات العامة"],
                ["المحور الفني"],
                ["المحور الإداري"],
                ["المحور الهندسي"]
            ]
            
        if self._destinations_list is None:
            self._destinations_list = [
                ["الإيعاز الى ادارة المستشفى بما يلي:"],
                ["الإيعاز الى ادارة القطاع بما يلي:"],
                ["الإيعاز الى ادارة المركز بما يلي:"],
                ["الإيعاز الى قسم الامور الادارية والقانونية والمالية بما يلي:"],
                ["الإيعاز الى شعبة التحقيقات/ قسمنا بما يلي:"],
                ["اكتب جهة الإيعاز يدوياً"],
                ["لا توجد توصية"],
                ["رجوع الى القائمة السابقة"]
            ]
        
        if self._section_presets is None:
            self._section_presets = {
                "المحور الفني": [
                    ["الأطباء"], ["الصيدلية"], ["المختبر"],
                    ["الأشعة"], ["التمريض"],
                    ["اكتب اسم القسم يدوياً"], ["رجوع"]
                ],
                "المحور الإداري": [
                    ["الإدارة والسجلات"], ["وحدة البصمة"],
                    ["اكتب اسم القسم يدوياً"], ["رجوع"]
                ],
                "المحور الهندسي": [
                    ["الاجهزة الطبية"], ["الصيانة"], ["الدفاع المدني"],
                    ["اكتب اسم القسم يدوياً"], ["رجوع"]
                ]
            }
    
    def get_axes_list(self):
        return self._axes_list or []
    
    def get_destinations_list(self):
        return self._destinations_list or []
    
    def get_section_presets(self):
        return self._section_presets or {}
    
    @asynccontextmanager
    async def get_db_connection(self):
        conn = await self.db_pool.acquire()
        try:
            yield conn
        finally:
            await self.db_pool.release(conn)
    
    async def create_visit(self, institution_name: str, visit_date: str,
                          manager_id: int, leader_id: Optional[int] = None) -> int:
        async with self.get_db_connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    INSERT INTO Visits (institution_name, visit_date, manager_id, leader_id, status)
                    VALUES (%s, %s, %s, %s, %s) RETURNING id
                """, (institution_name, visit_date, manager_id, leader_id, VisitStatus.OPEN.value))
                result = await cursor.fetchone()
                return result[0]
    
    async def join_visit(self, visit_id: int, user_id: int, user_name: str) -> bool:
        async with self.get_db_connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT status FROM Visits WHERE id = %s", (visit_id,))
                row = await cursor.fetchone()
                if not row or row[0] == VisitStatus.CLOSED.value:
                    return False
                
                await cursor.execute("""
                    INSERT INTO Visit_Members (visit_id, user_id, user_name)
                    VALUES (%s, %s, %s) ON CONFLICT (visit_id, user_id) DO NOTHING
                """, (visit_id, user_id, user_name))
                return True
    
    async def add_report(self, visit_id: int, user_id: int, axis_name: str,
                        section_name: str, notes: str,
                        rec_destination: Optional[str] = None,
                        recommendations: Optional[str] = None) -> int:
        async with self.get_db_connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    INSERT INTO Reports (visit_id, user_id, axis_name, section_name,
                                       notes, rec_destination, recommendations)
                    VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
                """, (visit_id, user_id, axis_name, section_name, notes,
                     rec_destination, recommendations))
                result = await cursor.fetchone()
                return result[0]
    
    async def get_user_reports_count(self, visit_id: int, user_id: int) -> int:
        async with self.get_db_connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT COUNT(*) FROM Reports WHERE visit_id = %s AND user_id = %s",
                    (visit_id, user_id)
                )
                result = await cursor.fetchone()
                return result[0] if result else 0
    
    async def save_draft(self, user_id: int, visit_id: int, user_name: str,
                        state: int, payload: Dict[str, Any]) -> None:
        payload_json = json.dumps(payload, ensure_ascii=False)
        async with self.get_db_connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    INSERT INTO Drafts (visit_id, user_id, user_name, state, payload)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (visit_id, user_id) DO UPDATE SET
                        state = EXCLUDED.state,
                        payload = EXCLUDED.payload,
                        updated_at = CURRENT_TIMESTAMP
                """, (visit_id, user_id, user_name, str(state), payload_json))
    
    async def delete_draft(self, user_id: int, visit_id: int) -> None:
        async with self.get_db_connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM Drafts WHERE visit_id = %s AND user_id = %s",
                    (visit_id, user_id)
                )
    
    async def queue_notification(self, admin_id: int, message: str) -> bool:
        if not self.message_queue:
            return False
        try:
            await self.message_queue.enqueue_task('notifications', {
                'admin_id': admin_id,
                'message': message
            })
            return True
        except Exception as e:
            logger.error(f"Failed to queue notification: {e}")
            return False
