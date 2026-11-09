"""Схемы сообщений, публикуемых в брокер.

Сообщение payments.new создаётся outbox-релизом из полезной нагрузки
outbox-записи; consumer валидирует его в PaymentsNewMessage.
"""

import uuid

from pydantic import BaseModel, Field


class PaymentsNewMessage(BaseModel):
    """Сообщение о новом платеже в очереди payments.new."""

    payment_id: uuid.UUID = Field(description="Идентификатор платежа к обработке")
