"""
QA plans cached per API version, so pages do not regenerate them (an LLM call)
on every visit.
"""
from typing import Dict, Optional

from sqlalchemy import delete, select

from app import db
from app.db import qa_plans


def _iso(value) -> Optional[str]:
    return value.isoformat() if value else None


class QAPlanService:

    def save_qa_plan(self, repository: str, api_name: str, version: int, qa_plan: Dict,
                     api_doc_hash: Optional[str] = None) -> bool:
        with db.engine().begin() as conn:
            db.upsert(conn, qa_plans, {
                "repo": repository, "api": api_name, "version": int(version),
                "plan": qa_plan, "api_doc_hash": api_doc_hash, "generated_at": db.utcnow(),
            }, ("repo", "api", "version"))
        return True

    def get_qa_plan(self, repository: str, api_name: str, version: int) -> Optional[Dict]:
        """{api_name, api_version, repository, generated_at, api_doc_hash, plan} or None."""
        with db.engine().connect() as conn:
            row = conn.execute(select(qa_plans).where(
                qa_plans.c.repo == repository, qa_plans.c.api == api_name, qa_plans.c.version == int(version))).first()
        if row is None:
            return None
        return {"api_name": row.api, "api_version": row.version, "repository": row.repo,
                "generated_at": _iso(row.generated_at), "api_doc_hash": row.api_doc_hash, "plan": row.plan}

    def plan_exists(self, repository: str, api_name: str, version: int) -> bool:
        return self.get_qa_plan(repository, api_name, version) is not None

    def get_all_plans_for_api(self, repository: str, api_name: str) -> Dict[int, Dict]:
        with db.engine().connect() as conn:
            rows = conn.execute(select(qa_plans).where(qa_plans.c.repo == repository, qa_plans.c.api == api_name))
            return {r.version: {"generated_at": _iso(r.generated_at), "is_template": (r.plan or {}).get("is_template", False)}
                    for r in rows}

    def delete_qa_plan(self, repository: str, api_name: str, version: int) -> bool:
        with db.engine().begin() as conn:
            return conn.execute(delete(qa_plans).where(
                qa_plans.c.repo == repository, qa_plans.c.api == api_name, qa_plans.c.version == int(version))).rowcount > 0

    def get_generation_info(self, repository: str, api_name: str, version: int) -> Optional[Dict]:
        plan = self.get_qa_plan(repository, api_name, version)
        if plan:
            return {"generated_at": plan["generated_at"], "is_template": (plan["plan"] or {}).get("is_template", False)}
        return None
