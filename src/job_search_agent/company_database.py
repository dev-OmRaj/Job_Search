from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class CompanyBase(DeclarativeBase):
    pass


class CompanyPortalRecord(CompanyBase):
    __tablename__ = "company_portals"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_name: Mapped[str] = mapped_column(String(250), index=True)
    job_portal_url: Mapped[str] = mapped_column(Text, unique=True, index=True)


class CompanyDirectory:
    """A separate, minimal company-to-job-portal SQLite directory."""

    def __init__(self, url: str):
        if url.startswith("sqlite:///"):
            db_path = Path(url.removeprefix("sqlite:///"))
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url)

    def create_schema(self) -> None:
        CompanyBase.metadata.create_all(self.engine)

    @staticmethod
    def _canonical_url(portal_url: str) -> str:
        parsed = urlsplit(portal_url.strip())
        hostname = (parsed.hostname or "").lower().rstrip(".").removeprefix("www.")
        if not hostname:
            return ""
        port = parsed.port
        netloc = hostname if port is None else f"{hostname}:{port}"
        path = parsed.path.rstrip("/")
        return urlunsplit((parsed.scheme.lower() or "https", netloc, path, "", ""))

    @staticmethod
    def _known_company_name(portal_url: str) -> str | None:
        hostname = (urlsplit(portal_url).hostname or "").lower().removeprefix("www.")
        if hostname == "amazon.jobs":
            return "Amazon"
        return None

    @staticmethod
    def _fallback_company_name(portal_url: str) -> str:
        return (urlsplit(portal_url).hostname or "Unknown company").removeprefix("www.")

    @staticmethod
    def _best_company_name(company_names: Iterable[str]) -> str | None:
        cleaned = [" ".join(name.split()) for name in company_names if name.strip()]
        if not cleaned:
            return None
        counts = Counter(name.casefold() for name in cleaned)
        winning_key, _ = counts.most_common(1)[0]
        return next(name for name in cleaned if name.casefold() == winning_key)

    def upsert(self, portal_url: str, company_names: Iterable[str] = ()) -> None:
        canonical_url = self._canonical_url(portal_url)
        if not canonical_url:
            raise ValueError("Job portal URL cannot be empty")
        discovered_name = self._best_company_name(company_names)
        known_name = self._known_company_name(canonical_url)

        with Session(self.engine) as session:
            record = session.scalar(
                select(CompanyPortalRecord).where(
                    CompanyPortalRecord.job_portal_url == canonical_url
                )
            )
            if record is None:
                record = CompanyPortalRecord(
                    company_name=(
                        known_name or discovered_name or self._fallback_company_name(canonical_url)
                    ),
                    job_portal_url=canonical_url,
                )
                session.add(record)
            else:
                if known_name or discovered_name:
                    record.company_name = known_name or discovered_name or record.company_name
            session.commit()

    def list_portals(self) -> list[dict[str, str]]:
        with Session(self.engine) as session:
            records = session.scalars(
                select(CompanyPortalRecord).order_by(
                    CompanyPortalRecord.company_name,
                    CompanyPortalRecord.job_portal_url,
                )
            ).all()
            return [
                {
                    "company_name": record.company_name,
                    "job_portal_url": record.job_portal_url,
                }
                for record in records
            ]
