"""Dependencies shared by the API and the dashboard; tests override them."""

from fastapi import Depends

from app.config import Settings, get_settings
from app.db import Supabase
from app.deepgram import DeepgramClient


def get_db(settings: Settings = Depends(get_settings)) -> Supabase:
    return Supabase(settings.supabase_url, settings.supabase_service_key.get_secret_value())


def get_deepgram(settings: Settings = Depends(get_settings)) -> DeepgramClient:
    return DeepgramClient(settings.deepgram_api_key.get_secret_value(), model=settings.deepgram_model,
                          base_url=settings.deepgram_base_url)
