import io
import re
import threading
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import service
from .certificate import generate_certificate_pdf
from .config import Settings
from .database import Base, make_engine, make_session_factory
from .models import Certificate, CertificateStatus, Job, JobStatus
from .schemas import CertificatePage, JobCreate, JobOut, JobPage
from .validation import validate_recipients


def _slug(text: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-").lower() or "certificate"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)

    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.resume_on_startup:
            # Resume interrupted jobs without delaying startup.
            threading.Thread(
                target=service.recover_unfinished_jobs,
                args=(session_factory, settings.storage_dir, app.state.generator),
                daemon=True,
                name="job-recovery",
            ).start()
        yield
        engine.dispose()

    app = FastAPI(
        title="Bulk Certificate Generator",
        version="1.0.0",
        description="Submit many recipients in one request; certificates are generated "
        "in the background. Poll the job, then download PDFs individually or as a ZIP.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    # Injectable so tests can simulate failures.
    app.state.generator = generate_certificate_pdf

    def get_db():
        with session_factory() as db:
            yield db

    def get_job_or_404(db: Session, job_id: str) -> Job:
        job = db.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        return job

    def get_certificate_file(cert: Certificate) -> Path:
        if cert.status != CertificateStatus.SUCCEEDED.value or not cert.file_path:
            raise HTTPException(
                409, f"Certificate is not available (status: {cert.status})"
            )
        root = settings.storage_dir.resolve()
        path = (root / cert.file_path).resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(404, "Certificate file is missing from storage")
        return path

    # ------------------------------------------------------------------ routes
    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/jobs", response_model=JobOut, status_code=202)
    def create_job(
        payload: JobCreate,
        request: Request,
        response: Response,
        background_tasks: BackgroundTasks,
        db: Session = Depends(get_db),
    ):
        if len(payload.recipients) > settings.max_recipients_per_job:
            raise HTTPException(
                422,
                f"Too many recipients: {len(payload.recipients)} "
                f"(maximum {settings.max_recipients_per_job} per job)",
            )

        valid, invalid = validate_recipients(payload.recipients)
        if not valid:
            raise HTTPException(
                422,
                detail={
                    "message": "No valid recipients in request",
                    "errors": [
                        {"index": r.index, "name": r.name, "error": r.error}
                        for r in invalid
                    ],
                },
            )

        job = service.create_job(
            db,
            event_name=payload.event_name,
            issuer=payload.issuer,
            issue_date=payload.issue_date or datetime.now(timezone.utc).date(),
            valid=valid,
            invalid=invalid,
        )
        background_tasks.add_task(
            service.process_job,
            job.id,
            session_factory,
            settings.storage_dir,
            request.app.state.generator,
        )
        response.headers["Location"] = f"/jobs/{job.id}"
        return job

    @app.get("/jobs", response_model=JobPage)
    def list_jobs(
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
        db: Session = Depends(get_db),
    ):
        total = db.scalar(select(func.count()).select_from(Job))
        items = db.scalars(
            select(Job).order_by(Job.created_at.desc()).limit(limit).offset(offset)
        ).all()
        return JobPage(total=total, limit=limit, offset=offset, items=items)

    @app.get("/jobs/{job_id}", response_model=JobOut)
    def get_job(job_id: str, db: Session = Depends(get_db)):
        return get_job_or_404(db, job_id)

    @app.get("/jobs/{job_id}/certificates", response_model=CertificatePage)
    def list_certificates(
        job_id: str,
        status: CertificateStatus | None = None,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        db: Session = Depends(get_db),
    ):
        get_job_or_404(db, job_id)
        query = select(Certificate).where(Certificate.job_id == job_id)
        if status:
            query = query.where(Certificate.status == status.value)
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        items = db.scalars(
            query.order_by(Certificate.index).limit(limit).offset(offset)
        ).all()
        return CertificatePage(total=total, limit=limit, offset=offset, items=items)

    @app.get("/jobs/{job_id}/download")
    def download_job_zip(job_id: str, db: Session = Depends(get_db)):
        job = get_job_or_404(db, job_id)
        if not JobStatus(job.status).is_finished:
            raise HTTPException(
                409, f"Job is still {job.status}; try again when it has finished"
            )
        certs = db.scalars(
            select(Certificate)
            .where(
                Certificate.job_id == job_id,
                Certificate.status == CertificateStatus.SUCCEEDED.value,
            )
            .order_by(Certificate.index)
        ).all()
        if not certs:
            raise HTTPException(404, "Job has no generated certificates")

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for cert in certs:
                archive.write(
                    get_certificate_file(cert),
                    f"{cert.index + 1:04d}-{_slug(cert.recipient_name)}.pdf",
                )
        return Response(
            buffer.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="certificates-{job_id}.zip"'
            },
        )

    @app.get("/certificates/{certificate_id}/download")
    def download_certificate(certificate_id: str, db: Session = Depends(get_db)):
        cert = db.get(Certificate, certificate_id)
        if cert is None:
            raise HTTPException(404, "Certificate not found")
        path = get_certificate_file(cert)
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=f"certificate-{_slug(cert.recipient_name)}.pdf",
        )

    return app
