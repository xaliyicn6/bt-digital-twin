# T-03 认证与双令牌

| 项 | 内容 |
| --- | --- |
| 阶段 | P1 |
| 依赖前置 | T-02 |
| 产出 | 登录、刷新、登出、jti 黑名单、kid 双密钥轮换、鉴权中间件 |
| 验收门 | 401/403 分支齐全；刷新即轮换可用；黑名单生效 |
| 预估工作量 | 4 人日 / 3 卡 |
| 人工主导 | 否 |
| 外部阻塞 | 无 |

## 1. 目标

实现 Access(RS256,30min) + Refresh(不透明串,8h) 的双令牌体系，支持撤销与密钥轮换。

## 2. 输入

- V1.2 §5.4.4 令牌规定
- sys_user / sys_role / sys_user_role
- Redis（黑名单与限流桶）

## 3. 改动范围

- 允许：backend/app/modules/auth/**、core/security/**、core/middleware/**
- 禁止：修改 sys_* 表结构

超出上述范围即视为越权改动，即使代码正确也退回。

## 4. 实现要点

- Refresh 存 Redis，键 `rt:{userId}:{deviceFp}`，刷新时旧值原子删除（Lua 保证）
- Access 校验必须查 `kid` 对应公钥，双密钥并存 7 天
- jti 黑名单 TTL = token 剩余寿命，不设固定 30 分钟
- 认证失败统一返回 401 + BT-20001（缺失）/ BT-20002（过期），不得暴露具体原因细节

## 5. DoD（可验证的完成定义）

- 登录返回 access+refresh，`sub/role/scope/project_ids/jti/exp/iat` 齐全
- 刷新后旧 refresh 立即失效（重放返回 401）
- 登出后原 access 在黑名单期内被拒
- 单测覆盖 401 两种错误码与 403 越权

## 6. 验收用例

| # | 输入 | 操作 | 期望结果 |
| --- | --- | --- | --- |
| 1 | 错误口令登录 | POST /auth/login | 401，code=BT-20001，detail 不区分“用户不存在”与“口令错误” |
| 2 | 用已登出的 access 调 /bridges | GET | 401，code=BT-20002 |
| 3 | 用 refresh 调业务接口 | GET /bridges | 401，refresh 不得充当 access |

## 7. 依赖与顺序

T-02 之后，T-04 之前。

## 8. 常见坑

- 不要把 refresh 放 localStorage 明文长期保存
- 不要在 JWT 里塞权限点全量，scope 只放模块级
- 不要用对称密钥省事，契约要求 RS256
