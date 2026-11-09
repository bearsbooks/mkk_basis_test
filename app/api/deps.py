"""Общие зависимости FastAPI: аутентификация по статическому API-ключу.

Все эндпоинты API защищены зависимостью verify_api_key — она проверяет
заголовок X-API-Key и отклоняет запросы без него или с неверным значением.
"""

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.config import Settings, get_settings


async def verify_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,
) -> None:
    """Проверяет статический API-ключ из заголовка X-API-Key.

    Сравнение выполняется через secrets.compare_digest, чтобы исключить
    timing-атаки на побайтовое сравнение. При отсутствии или несовпадении
    ключа возвращается 401 Unauthorized.
    """
    if x_api_key is None or not secrets.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Недействительный или отсутствующий API-ключ",
            headers={"WWW-Authenticate": "ApiKey"},
        )
