"""
DriverFunction model - Driver job functions/roles.

Maps to mova.driver_function table.
Defines available driver roles (e.g., "Motorista", "Operador", etc.).
"""

from sqlalchemy import Column, Integer, String

from app.core.database import Base


class DriverFunction(Base):
    """
    Driver function/role model.

    Represents job functions available for drivers.
    """

    __tablename__ = "driver_function"
    __table_args__ = {'schema': 'mova'}

    # Primary key
    id = Column('id', Integer, primary_key=True, index=True)

    # Function details
    name = Column('name', String, nullable=True)
    account_id = Column('account_id', Integer, nullable=True)

    # Status
    status = Column('status', Integer, nullable=False, default=1)

    # Virtual properties
    @property
    def is_active(self) -> bool:
        """Check if function is active."""
        return self.status == 1

    @property
    def is_deleted(self) -> bool:
        """Check if function is soft-deleted."""
        return self.status == -1

    def __repr__(self):
        return f"<DriverFunction(id={self.id}, name={self.name}, status={self.status})>"
