"""Schemas de videotelemetria."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class VideoDeviceResponse(BaseModel):
    """Equipamento de vídeo instalado num veículo."""

    id: int
    unit_id: int
    device_id: Optional[int] = None
    vehicle_label: Optional[str] = None
    vehicle_prefix: Optional[str] = None
    device_serial: Optional[str] = None
    device_imei: Optional[str] = None
    association_date: Optional[datetime] = None
    release_date: Optional[datetime] = None
    status: Optional[int] = None
    last_communication: Optional[datetime] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    speed: Optional[int] = None
    ignition: Optional[bool] = None
    online: bool
    #: De onde veio a conclusão de online/offline. Declarado porque é
    #: aproximação: o DVR não reporta separadamente do rastreador.
    status_source: str

    model_config = {"from_attributes": True}


class VideoOccurrenceResponse(BaseModel):
    id: int
    vehicle_id: Optional[int] = None
    vehicle_label: Optional[str] = None
    vehicle_prefix: Optional[str] = None
    event_type: Optional[str] = None
    #: dms (motorista), adas (condução) ou equipamento (falha da câmera).
    category: str
    severity: Optional[str] = None
    timestamp: Optional[datetime] = None
    description: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    data: Optional[dict[str, Any]] = None
    #: Se há mídia vinculada. Sem clipe não há o que revisar.
    has_clip: bool = False
    acknowledged: Optional[bool] = None
    acknowledged_by: Optional[int] = None
    acknowledged_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class VideoOccurrenceSummary(BaseModel):
    total: int
    pending: int
    dms: int
    adas: int
    equipment: int
    vehicles: int


class VideoOccurrenceListResponse(BaseModel):
    items: list[VideoOccurrenceResponse]
    total: int
    summary: VideoOccurrenceSummary
    limit: int
    offset: int
