"""Seed demo tenant + users for local development (run once after migrations)."""

import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (registers all mappers)
from app.base_models import Tenant, User, UserRole
from app.core.config import settings
from app.core.security import get_password_hash, hash_pii_for_lookup

USERS = [
    ("demo@stratum.ai", "demo1234", "Demo Admin", UserRole.ADMIN),
    ("superadmin@stratum.ai", "Admin123!", "Super Admin", UserRole.SUPERADMIN),
]


async def main() -> None:
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        result = await db.execute(select(Tenant).where(Tenant.slug == "demo"))
        tenant = result.scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(
                name="Demo Company",
                slug="demo",
                plan="professional",
                settings={"timezone": "UTC", "currency": "USD"},
            )
            db.add(tenant)
            await db.flush()
            print(f"created tenant {tenant.slug} (id={tenant.id})")

        for email, password, name, role in USERS:
            email_hash = hash_pii_for_lookup(email)
            result = await db.execute(select(User).where(User.email_hash == email_hash))
            if result.scalar_one_or_none():
                print(f"user {email} already exists")
                continue
            user = User(
                tenant_id=tenant.id,
                email=email,
                email_hash=email_hash,
                password_hash=get_password_hash(password),
                full_name=name,
                role=role,
                is_active=True,
                is_verified=True,
            )
            db.add(user)
            print(f"created user {email} ({role.value})")

        await db.commit()
    await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # pragma: no cover
        print(f"seed failed: {exc}", file=sys.stderr)
        sys.exit(1)
