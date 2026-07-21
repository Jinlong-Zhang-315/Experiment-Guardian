"""实验草稿和 Artifact 上传声明的持久化查询。"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from experiment_guardian.domain.enums import SubmissionStatus
from experiment_guardian.infrastructure.models import (
    Artifact,
    ExperimentSubmission,
    RunManifest,
    SubmissionRisk,
)


class SqlAlchemySubmissionRepository:
    @staticmethod
    def get_manifest(session: Session, manifest_id: UUID) -> RunManifest | None:
        return session.get(RunManifest, manifest_id)

    @staticmethod
    def get_submission(session: Session, submission_id: UUID) -> ExperimentSubmission | None:
        return session.get(ExperimentSubmission, submission_id)

    @staticmethod
    def get_submission_for_update(
        session: Session, submission_id: UUID
    ) -> ExperimentSubmission | None:
        return session.scalar(
            select(ExperimentSubmission)
            .where(ExperimentSubmission.id == submission_id)
            .with_for_update()
        )

    @staticmethod
    def list_artifacts(session: Session, submission_id: UUID) -> list[Artifact]:
        return list(
            session.scalars(
                select(Artifact)
                .where(Artifact.submission_id == submission_id)
                .order_by(Artifact.artifact_type, Artifact.filename)
            ).all()
        )

    @staticmethod
    def list_artifacts_for_update(session: Session, submission_id: UUID) -> list[Artifact]:
        return list(
            session.scalars(
                select(Artifact)
                .where(Artifact.submission_id == submission_id)
                .order_by(Artifact.artifact_type, Artifact.filename)
                .with_for_update()
            ).all()
        )

    @staticmethod
    def list_analysis_candidates(
        session: Session, *, project_id: UUID, exclude_submission_id: UUID
    ) -> list[ExperimentSubmission]:
        """结构化过滤先于内容相似性；R11 最多检查最近 200 个已验证草稿。"""

        return list(
            session.scalars(
                select(ExperimentSubmission)
                .where(
                    ExperimentSubmission.project_id == project_id,
                    ExperimentSubmission.id != exclude_submission_id,
                    ExperimentSubmission.status.in_(
                        {
                            SubmissionStatus.UPLOAD_VERIFIED,
                            SubmissionStatus.PROCESSING,
                            SubmissionStatus.NEEDS_REVIEW,
                            SubmissionStatus.APPROVED,
                        }
                    ),
                    ExperimentSubmission.upload_verified_at.is_not(None),
                )
                .order_by(ExperimentSubmission.created_at.desc())
                .limit(200)
            ).all()
        )

    @staticmethod
    def list_risks(session: Session, submission_id: UUID) -> list[SubmissionRisk]:
        return list(
            session.scalars(
                select(SubmissionRisk)
                .where(SubmissionRisk.submission_id == submission_id)
                .order_by(SubmissionRisk.created_at, SubmissionRisk.id)
            ).all()
        )
