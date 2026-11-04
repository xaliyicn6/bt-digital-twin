# T-02 脚手架与 CI

| 项 | 内容 |
| --- | --- |
| 阶段 | P1 |
| 依赖前置 | T-01 |
| 产出 | backend 与 frontend 可启动骨架、ruff/mypy/eslint/prettier 配置、CI 流水线、.env.example |
| 验收门 | `make up` 起空壳服务返回 /healthz 200；CI 四道闸（lint、type、test、contract）全绿 |
| 预估工作量 | 3 人日 / 3 卡 |
| 人工主导 | 否 |
| 外部阻塞 | 无 |

## 1. 目标

让后续每张卡都能在 10 分钟内跑通“改代码—跑测试—过闸”的闭环。

## 2. 输入

- AGENTS.md 的目录结构与规范
- contracts/openapi.yaml（用于生成客户端与契约测试）

## 3. 改动范围

- 允许：backend/**、frontend/**、tests/**、.github/**、Makefile、pyproject.toml、package.json
- 禁止：contracts/**、db/schema.sql、state/**

超出上述范围即视为越权改动，即使代码正确也退回。

## 4. 实现要点

- `/healthz` 不查依赖，`/readyz` 查 DB 与 MinIO；两者不可合并
- 配置用 pydantic-settings 从环境变量读取，缺必填项启动即失败，不允许默认值兜底密钥
- CI 必须先跑契约校验再跑单测，契约不过直接 fail
- 日志初始化在 `core/logging.py`，含 traceId 注入中间件

## 5. DoD（可验证的完成定义）

- `make up` 后 `GET /healthz` 返回 200 且响应含 `traceId`
- 空跑 `pytest` 有 1 个 healthz 用例并通过
- CI 在 PR 上跑出 4 个 job 且全绿
- 前端 `npm run build` 产物可在 Nginx 静态托管

## 6. 验收用例

| # | 输入 | 操作 | 期望结果 |
| --- | --- | --- | --- |
| 1 | 未设置 DATABASE_URL | 启动应用 | 进程退出码非 0，日志报缺失配置项名 |
| 2 | 请求 /healthz 时带 X-Request-Id: abc123 | 调用 | 响应体 traceId=abc123，日志同值 |
| 3 | 提交一个 mypy 报错的 PR | 触发 CI | type job 失败并阻断合并 |

## 7. 依赖与顺序

紧随 T-01。T-03 至 T-24 全部依赖它。

## 8. 常见坑

- 不要让 /healthz 查数据库，否则依赖抖动会导致重启风暴
- 不要提交 .env
- 不要在前端手写 axios 实例，统一用生成客户端
