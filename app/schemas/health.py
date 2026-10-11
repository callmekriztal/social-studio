from datetime import datetime
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Schema for health status response."""

    status: str = Field(..., description="Overall health status of the application")
    environment: str = Field(..., description="Current deployment environment")
    database: str = Field(..., description="Database connection health status")
    timestamp: datetime = Field(..., description="Server timestamp of the check")
