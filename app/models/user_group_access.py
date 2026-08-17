"""
User Group Access model - Maps to existing mova.user_group_access table.
Controls which groups/subgroups each user can access.
"""

from sqlalchemy import Column, Integer

from app.core.database import Base


class UserGroupAccess(Base):
    """
    User Group Access model mapped to existing mova.user_group_access table.

    Defines the access control mapping between users and groups/subgroups.
    A user can access resources (vehicles, devices, etc.) only from the groups
    listed in this table.

    Attributes:
        id: Primary key
        user_id: Reference to mova.users.id
        group_id: Group identifier (matches mova.users.group_id)
        subgroup_id: Optional subgroup identifier (nullable)
    """

    __tablename__ = "user_group_access"
    __table_args__ = {'schema': 'mova'}  # mova schema

    id = Column('id', Integer, primary_key=True, index=True)
    user_id = Column('user_id', Integer, nullable=True, index=True)
    group_id = Column('group_id', Integer, nullable=True, index=True)
    subgroup_id = Column('subgroup_id', Integer, nullable=True)

    def __repr__(self):
        return f"<UserGroupAccess(id={self.id}, user_id={self.user_id}, group_id={self.group_id}, subgroup_id={self.subgroup_id})>"
