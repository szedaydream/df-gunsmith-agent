# -*- coding: utf-8 -*-
# 这个空文件是 services 文件夹的"身份证"：
# 有它，Python 才把 services 当作一个「包」，外部才能写
# from services.ai_service import AIService

from services.ai_service import AIService
from services.search_service import get_search_service
from services.storage_service import StorageService

__all__ = ["AIService", "get_search_service", "StorageService"]
