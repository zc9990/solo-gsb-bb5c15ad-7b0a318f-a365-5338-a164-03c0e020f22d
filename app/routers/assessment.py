from sqlite3 import Connection

from fastapi import APIRouter, Depends, HTTPException

from ..database import get_db
from ..engine import assess_certificate_deactivation
from ..schemas import DeactivationAssessmentIn, DeactivationAssessmentOut

router = APIRouter(prefix="/api/assessments", tags=["assessment"])


@router.post(
    "/certificate-deactivation",
    response_model=DeactivationAssessmentOut,
    summary="证书停用影响评估（模拟，不写入数据库）",
)
def certificate_deactivation_impact(
    body: DeactivationAssessmentIn, db: Connection = Depends(get_db)
):
    """在内存中模拟移除待停用证书及其关联溯源边，重新评估受影响仪器的
    唯一链 / 多候选 / 断裂状态，返回停用前后状态、替代链摘要与新增冲突。

    - ``instrument_codes`` 留空时自动覆盖全部溯源路径触及被停用证书的仪器；
      给出时仅评估指定仪器（未受影响的仪器会以 ``impact=unchanged`` 返回）。
    - 整个过程不执行任何写入，数据库内容保持不变。
    """
    try:
        return assess_certificate_deactivation(
            db,
            at=body.at,
            deactivated_cert_ids=body.deactivated_certificate_ids,
            instrument_codes=body.instrument_codes,
        )
    except KeyError as exc:
        kind, values = exc.args[0]
        if kind == "certificate":
            raise HTTPException(
                422,
                f"待停用证书不存在（未登记或已停用）: {', '.join(values)}",
            )
        if kind == "instrument":
            raise HTTPException(
                404, f"仪器不存在: {', '.join(values)}"
            )
        raise
