"""实验草稿和 Artifact 上传声明的持久化查询。"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from experiment_guardian.infrastructure.models import Artifact, ExperimentSubmission, RunManifest


class SqlAlchemySubmissionRepository:
    @staticmethod
    def get_manifest(session: Session, manifest_id: UUID) -> RunManifest | None:
        return session.get(RunManifest, manifest_id)

    @staticmethod
    def get_submission(session: Session, submission_id: UUID) -> ExperimentSubmission | None:
        return session.get(ExperimentSubmission, submission_id)

    @staticmethod
    def list_artifacts(session: Session, submission_id: UUID) -> list[Artifact]:
        return list(
            session.scalars(
                select(Artifact)
                .where(Artifact.submission_id == submission_id)
                .order_by(Artifact.artifact_type, Artifact.filename)
            ).all()
        )
