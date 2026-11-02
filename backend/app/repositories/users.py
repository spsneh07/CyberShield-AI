from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import User


class UserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, user_id: str) -> User | None:
        return self.db.get(User, user_id)

    def get_by_token_hash(self, token_hash: str) -> User | None:
        stmt = select(User).where(User.token_hash == token_hash)
        return self.db.scalars(stmt).first()

    def create_with_token(self, token_hash: str, display_name: str | None = None) -> User:
        user = User(token_hash=token_hash, display_name=display_name)
        self.db.add(user)
        self.db.commit()
        return user

