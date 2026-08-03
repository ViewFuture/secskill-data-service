"""猎聘 CLI 输出字段模型与标准化。"""

from __future__ import annotations

from typing import Any


FORBIDDEN_CLI_TOKENS: frozenset[str] = frozenset(
    {
        "apply",
        "resume",
        "auth",
        "skill",
        "update-",
        "add-",
    }
)

DEFAULT_WARNINGS: tuple[str, ...] = (
    "SINGLE_SOURCE_EVIDENCE",
    "NO_JOB_DESCRIPTION",
    "NO_PUBLISH_DATE",
    "TITLE_RELEVANCE_FILTER_REQUIRED",
)


def normalize_liepin_job(raw: dict[str, Any]) -> dict[str, Any] | None:
    """将猎聘原始字段映射为网关统一岗位结构。"""
    if not isinstance(raw, dict):
        return None
    job_id = str(raw.get("jobId") or raw.get("job_id") or "").strip()
    job_title = str(raw.get("jobName") or raw.get("job_title") or "").strip()
    company = str(raw.get("company") or "").strip()
    if not job_title or not company:
        return None
    return {
        "job_id": job_id,
        "job_type": str(raw.get("jobType") or raw.get("job_type") or "").strip(),
        "job_title": job_title,
        "company": company,
        "region": str(raw.get("location") or raw.get("region") or "").strip(),
        "salary": str(raw.get("salary") or "").strip(),
        "education": str(raw.get("education") or "").strip(),
        "work_years": str(raw.get("workYears") or raw.get("work_years") or "").strip(),
        "industry": str(raw.get("industry") or "").strip(),
        "company_tags": raw.get("companyTags")
        if isinstance(raw.get("companyTags"), list)
        else [],
        "financing_stage": str(
            raw.get("financingStage") or raw.get("financing_stage") or ""
        ).strip(),
        "company_size": str(
            raw.get("companySize") or raw.get("company_size") or ""
        ).strip(),
        "source_url": str(
            raw.get("jobDetailUrl") or raw.get("source_url") or ""
        ).strip(),
        "source_code": "liepin",
        "source_name": "猎聘",
        "description": "",
        "publish_date": None,
        "publish_date_raw": "",
        "trend_eligible": False,
        "trend_claim_allowed": False,
        "skill_claim_allowed": False,
    }


def extract_job_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """从 CLI JSON payload 提取岗位列表。"""
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("jobs", "list", "items", "records", "jobList"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    for key in ("jobs", "list", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []
