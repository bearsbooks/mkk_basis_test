"""Базовый класс декларативных ORM-моделей проекта."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Родительский класс всех ORM-моделей.

    Наследование от DeclarativeBase (стиль SQLAlchemy 2.0) включает
    аннотационный маппинг и единый реестр таблиц в Base.metadata.
    """
