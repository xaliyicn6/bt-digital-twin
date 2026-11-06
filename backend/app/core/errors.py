"""统一错误码登记表：problem+json 的 code 必须在此登记（typo 在 import 时即暴露）。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ErrorSpec:
    code: str
    status: int
    title: str
    type_uri: str


ERRORS: dict[str, ErrorSpec] = {
    "BT-10001": ErrorSpec("BT-10001", 401, "未认证", "https://bt.local/problem/unauthenticated"),
    "BT-10002": ErrorSpec(
        "BT-10002", 400, "参数缺失或格式错误", "https://bt.local/problem/bad-request"
    ),
    # 与 BT-10001（401 未认证）刻意分离：前端若把 422 当登录失效会误触发重新登录
    "BT-10003": ErrorSpec(
        "BT-10003", 422, "请求参数校验失败", "https://bt.local/problem/validation"
    ),
    "BT-30001": ErrorSpec("BT-30001", 404, "构件不存在", "https://bt.local/problem/not-found"),
    "BT-30002": ErrorSpec("BT-30002", 409, "构件编码重复", "https://bt.local/problem/conflict"),
    "BT-30003": ErrorSpec(
        "BT-30003", 422, "层级或父节点非法", "https://bt.local/problem/validation"
    ),
    "BT-30004": ErrorSpec("BT-30004", 409, "编码流水号已用尽", "https://bt.local/problem/conflict"),
    # 构件存在但点云/瓦片文件缺失：比 BT-30001（构件不存在）更贴切
    "BT-30005": ErrorSpec("BT-30005", 404, "点云文件不存在", "https://bt.local/problem/not-found"),
    "BT-40001": ErrorSpec(
        "BT-40001", 422, "几何 SRID 不一致", "https://bt.local/problem/validation"
    ),
    # 设计说明书 5.4：点云密度不足，无法完成几何分析
    "BT-40002": ErrorSpec("BT-40002", 422, "点云密度不足", "https://bt.local/problem/validation"),
    # 几何任务查询：任务 id 不存在（与 BT-30001「构件不存在」区分）
    "BT-60003": ErrorSpec("BT-60003", 404, "几何任务不存在", "https://bt.local/problem/not-found"),
    # 设计断面参数非法：自交、退化、底板与拱腰圆弧无交点
    "BT-60004": ErrorSpec(
        "BT-60004", 422, "设计断面参数非法", "https://bt.local/problem/validation"
    ),
    # ---- 工单（M07）：先登记再使用，禁止在路由里出现未登记 code ----
    "BT-70001": ErrorSpec("BT-70001", 409, "工单状态非法迁移", "https://bt.local/problem/conflict"),
    "BT-70002": ErrorSpec("BT-70002", 409, "重复建单", "https://bt.local/problem/conflict"),
    # 源几何任务未 SUCCEEDED：AI 结果不能直接派单
    "BT-70003": ErrorSpec("BT-70003", 409, "源任务未成功", "https://bt.local/problem/conflict"),
    "BT-70004": ErrorSpec(
        "BT-70004", 422, "分析类型不支持派单", "https://bt.local/problem/validation"
    ),
    "BT-70005": ErrorSpec(
        "BT-70005", 403, "班组超出数据范围", "https://bt.local/problem/forbidden"
    ),
    "BT-70006": ErrorSpec("BT-70006", 404, "维修班组不存在", "https://bt.local/problem/not-found"),
    "BT-70007": ErrorSpec(
        "BT-70007", 422, "班组未配置负责人", "https://bt.local/problem/validation"
    ),
    "BT-70008": ErrorSpec("BT-70008", 404, "工单不存在", "https://bt.local/problem/not-found"),
    "BT-90001": ErrorSpec("BT-90001", 503, "依赖未就绪", "https://bt.local/problem/unavailable"),
}


@dataclass
class BizError(Exception):
    """业务异常。

    不能用 ``frozen=True``：异常抛出后解释器与 contextlib 需要给 ``__traceback__``
    赋值，冻结数据类会直接抛 FrozenInstanceError，把原始业务错误吞掉。
    """

    code: str
    status: int
    detail: str
    title: str = "业务校验失败"
    type_uri: str = "https://bt.local/problem/validation"
    errors: list[dict[str, str]] = field(default_factory=list)

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


def api_error(code: str, detail: str, errors: list[dict[str, str]] | None = None) -> BizError:
    """按登记表构造错误；未登记的 code 直接 KeyError，避免线上出现无主错误码。"""
    spec = ERRORS[code]
    return BizError(
        code=spec.code,
        status=spec.status,
        title=spec.title,
        type_uri=spec.type_uri,
        detail=detail,
        errors=errors or [],
    )
