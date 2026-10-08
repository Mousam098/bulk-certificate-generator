"""Job creation and processing logic (framework-independent)."""
import logging
from datetime import date
from pathlib import Path
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .certificate import CertificateData
from .models import Certificate, CertificateStatus, Job, JobStatus, utcnow
from .validation import InvalidRecipient, ValidRecipient

logger = logging.getLogger(__name__)

Generator = Callable[[Path, CertificateData], None]


def create_job(
    db: Session,
    *,
    event_name: str,
    issuer: str,
    issue_date: date,
    valid: list[ValidRecipient],
    invalid: list[InvalidRecipient],
) -> Job:
    """Persist a job and one Certificate row per submitted recipient.

    Invalid recipients are stored as already-failed certificates so the client can see
    exactly which rows were rejected and why, without losing the valid ones.
    """
    job = Job(
        status=JobStatus.PENDING.value,
        event_name=event_name,
        issuer=issuer,
        issue_date=issue_date,
        total=len(valid) + len(invalid),
        succeeded=0,
        failed=len(invalid),
    )
    for r in valid:
        job.certificates.append(
            Certificate(index=r.index, recipient_name=r.name, recipient_email=r.email)
        )
    for r in invalid:
        job.certificates.append(
            Certificate(
                index=r.index,
                recipient_name=r.name,
                recipient_email=r.email,
                status=CertificateStatus.FAILED.value,
                error=f"Validation failed - {r.error}",
            )
        )
    db.add(job)
    db.commit()
    return job


def _final_status(job: Job) -> JobStatus:
    if job.failed == 0:
        return JobStatus.COMPLETED
    if job.succeeded == 0:
        return JobStatus.FAILED
    return JobStatus.COMPLETED_WITH_ERRORS


def _generate_one(
    db: Session, job: Job, cert: Certificate, storage_dir: Path, generator: Generator
) -> None:
    relative = Path(job.id) / f"{cert.id}.pdf"
    try:
        generator(
            storage_dir / relative,
            CertificateData(
                certificate_id=cert.id,
                recipient_name=cert.recipient_name or "",
                event_name=job.event_name,
                issuer=job.issuer,
                issue_date=job.issue_date,
            ),
        )
    except Exception as exc:  # one bad certificate must not stop the job
        logger.exception("Certificate %s (job %s) failed", cert.id, job.id)
        cert.status = CertificateStatus.FAILED.value
        cert.error = f"Generation failed - {type(exc).__name__}: {exc}"[:500]
        job.failed += 1
    else:
        cert.status = CertificateStatus.SUCCEEDED.value
        cert.file_path = relative.as_posix()
        cert.generated_at = utcnow()
        job.succeeded += 1
    # Committed per certificate: progress is visible to pollers, and a crash loses
    # at most the certificate in flight (it is still PENDING and gets redone).
    db.commit()


def process_job(
    job_id: str,
    session_factory: sessionmaker,
    storage_dir: Path,
    generator: Generator,
) -> None:
    """Generate every still-pending certificate of a job. Safe to call again (resume)."""
    with session_factory() as db:
        job = db.get(Job, job_id)
        if job is None or JobStatus(job.status).is_finished:
            return
        try:
            job.status = JobStatus.PROCESSING.value
            job.started_at = job.started_at or utcnow()
            db.commit()

            pending = db.scalars(
                select(Certificate)
                .where(
                    Certificate.job_id == job_id,
                    Certificate.status == CertificateStatus.PENDING.value,
                )
                .order_by(Certificate.index)
            ).all()
            for cert in pending:
                _generate_one(db, job, cert, storage_dir, generator)

            job.status = _final_status(job).value
            job.finished_at = utcnow()
            db.commit()
        except Exception as exc:  # infrastructure failure (DB, disk...) - not per-certificate
            logger.exception("Job %s aborted", job_id)
            db.rollback()
            job = db.get(Job, job_id)
            job.status = JobStatus.FAILED.value
            job.error = f"{type(exc).__name__}: {exc}"[:500]
            job.finished_at = utcnow()
            db.commit()


def recover_unfinished_jobs(
    session_factory: sessionmaker, storage_dir: Path, generator: Generator
) -> int:
    """Resume jobs that were interrupted by a restart. Returns how many were resumed."""
    with session_factory() as db:
        job_ids = db.scalars(
            select(Job.id)
            .where(
                Job.status.in_([JobStatus.PENDING.value, JobStatus.PROCESSING.value])
            )
            .order_by(Job.created_at)
        ).all()
    for job_id in job_ids:
        process_job(job_id, session_factory, storage_dir, generator)
    return len(job_ids)
