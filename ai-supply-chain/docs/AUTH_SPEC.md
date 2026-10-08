# 认证与权限规格（AUTH-1，TASK-015）

> 本项目 D11 冻结了 7 张表，`users` 是其中最后一张（一直未建）。
> 项目文档对登录/密码/会话/权限**零规格**，只有 D3「V1 单租户：不做 tenants」。
> 本文是**首次**为认证定规格；定稿前不写实现。

## 1. 范围

**做**
- 账号密码登录 / 登出
- 会话保持（令牌）
- 角色区分（管理员 / 成员）
- 后端对所有业务接口做鉴权
- 前端登录页 + 路由守卫（未登录跳登录页）

**不做**（本期明确排除，避免范围蔓延）
- 注册（账号由管理员创建；V1 单租户内部系统，不开放自助注册）
- 第三方登录（OAuth / SSO / 企业微信）
- 找回密码（忘记密码 = 管理员重置）
- 多租户（D3 已冻结：不做 tenants）
- 细粒度权限矩阵（只分两级角色，不做按资源/按动作的权限表）
- 操作审计日志（另议；`customer_events` 已覆盖客户侧留痕）

## 2. 数据模型（users 表，D11 冻结表）

| 列 | 类型 | 说明 |
|---|---|---|
| id | BIGINT PK | |
| username | VARCHAR(64) UNIQUE | 登录名；**不区分大小写**存储统一小写 |
| display_name | VARCHAR(64) | 界面显示名 |
| password_hash | VARCHAR(255) | 格式 `pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>` |
| role | ENUM('ADMIN','MEMBER') | ADMIN 可管理账号；MEMBER 只能用业务功能 |
| is_active | BOOL | 停用后**不许登录**，且已发令牌立即失效（见 §5） |
| last_login_at | DATETIME(6) NULL | 最近一次登录成功时间 |
| created_at / updated_at | DATETIME(6) | 同其它表口径（D16） |

**不建的表**：`sessions`（会话状态放令牌里，见 §4）、`permissions`（两级角色足够）。

## 3. 口令存储

- 算法：**PBKDF2-HMAC-SHA256**，标准库 `hashlib.pbkdf2_hmac` 实现。
  ★ 理由：不新增第三方依赖（`bcrypt`/`argon2-cffi` 都能用，但要改依赖清单）；
  PBKDF2 是 NIST 认可的口令派生算法，配足够迭代次数对内部系统足够。
  若将来要求更强抗 GPU 能力，再评估 argon2id（需 D14 式的"不可替代理由"记录）。
- 迭代次数：**210_000**（OWASP 2023 对 PBKDF2-HMAC-SHA256 的建议下限 600k 是按纯口令场景；
  内部系统取 210k 并记录理由；★ 该值随硬件演进应上调，调整时旧哈希仍可验证——迭代次数写在哈希串里）。
- 盐：每个用户独立 16 字节随机（`secrets.token_bytes(16)`）。
- 比较：`hmac.compare_digest`（恒定时间），防计时侧信道。
- ★ **绝不存明文、绝不存可逆密文**；哈希串自带参数，便于将来平滑升级。

## 4. 会话方案：签名令牌（HMAC-SHA256），不落库

格式：`<base64url(payload_json)>.<base64url(hmac_sha256(payload, SECRET))>`

payload 字段：
```json
{"sub": <user_id>, "usr": "<username>", "rol": "ADMIN|MEMBER",
 "iat": <unix>, "exp": <unix>, "tv": <token_version>}
```

- **为什么不用 JWT 库**：只需签名+校验+过期，标准库 `hmac`/`hashlib`/`base64`/`json` 足够；
  引 `pyjwt` 属于"能用简单方式解决就不引依赖"（§五）。
- **为什么不落库**：落库就要 `sessions` 表（第 8 张表，超冻结范围）；
  无状态令牌 + §5 的失效机制已满足需求。
- 有效期：**12 小时**；不自动续期（内部系统，一天一次登录可接受）。
  ★ 不做 refresh token：那需要额外的令牌状态存储，复杂度收益不划算。
- SECRET：从 `settings.auth_secret_key` 读（`.env`），**不硬编码**；
  未配置时**拒绝启动鉴权**并给出明确错误（不允许静默用默认密钥）。

## 5. 令牌失效（无状态令牌的代价必须补上）

三个失效入口，缺一不可：
1. **过期**：`exp` 到期即失效。
2. **主动登出**：前端丢弃令牌即可（无状态）。★ 但这意味着**被盗令牌在过期前仍可用**——
   所以下面第 3 条必须有。
3. **强制失效**：`users.token_version` 自增。改密码 / 停用账号 / 管理员踢下线时 +1，
   令牌 payload 里的 `tv` 与之不符即拒绝。
   ★ 校验时会查一次用户（拿 `token_version` 和 `is_active`），所以**不是纯无状态**——
   这是刻意的取舍：多一次主键查询，换"停用能立即生效"。

## 6. 受保护范围

- **需要登录**：所有 `/api/*` 业务接口。
- **明确放行**（不鉴权）：
  - `POST /api/auth/login`（登录本身）
  - `GET  /api/health`（健康检查，运维探活用）
  - `GET  /api/docs`、`/api/openapi.json`（开发期文档；生产应关，见 §8）
- 未登录访问受保护接口 → **401** + `{"error": "unauthorized", "message": ...}`。
- 已登录但角色不足 → **403** + `{"error": "forbidden", ...}`。

## 7. 账号管理接口（仅 ADMIN）

- `POST /api/users` 创建账号（username / display_name / password / role）
- `GET  /api/users` 列表（**绝不返回 password_hash**）
- `POST /api/users/{id}/reset-password` 重置密码（同时 `token_version` +1）
- `POST /api/users/{id}/toggle-active` 停用/启用（停用时 `token_version` +1）
- `GET  /api/auth/me` 当前登录者信息（任何已登录角色可用）

## 8. 前端

- `/login` 登录页；未登录访问任何业务路由 → 重定向 `/login`。
- 令牌存 `localStorage`（★ 取舍说明：`localStorage` 可被 XSS 读取，`HttpOnly` Cookie 更安全，
  但 Cookie 方案要处理 CSRF，且需要前后端同域配置。V1 内部系统选 `localStorage` +
  较短有效期 + §5 的强制失效；将来对外暴露时应改为 HttpOnly Cookie + CSRF 令牌）。
- 请求拦截：统一附加 `Authorization: Bearer <token>`；收到 401 → 清令牌并跳登录页。
- 顶栏显示当前用户 `display_name` 与角色；下拉提供「退出登录」（真实生效，不再置灰）。

## 9. 初始化

- 首个管理员由**命令行脚本**创建（`scripts/create_admin.py`），不在接口里提供"首次注册"——
  避免部署后被人抢先注册管理员。
- 种子账号只在显式执行脚本时创建，**不写进迁移**（迁移里塞默认密码是安全反模式）。

## 10. 与既有决策的关系

- **D3（V1 单租户）**：不变。认证是"单租户内的多用户"，不是多租户；不做 tenants。
- **D11（冻结 7 表）**：本规格只新增 `users`（**在冻结清单内**），不新增其它表。
- **D25（users 暂不建）**：被本规格取代 —— 规格已定，故开始实现；D25 保留为历史记录。
- **D7（AI 不许越权）**：AI 相关接口同样需要登录；AI 不会以任何身份绕过鉴权。
