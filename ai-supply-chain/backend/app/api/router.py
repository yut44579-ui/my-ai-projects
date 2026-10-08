"""API 总路由（TASK-019 起分**公开**与**受保护**两组）。

═══════════════════════════════════════════════════════════════════════
【为什么分两组，而不是给每个路由加鉴权依赖】
═══════════════════════════════════════════════════════════════════════
    业务接口有 40+ 条。逐条加 `Depends(get_current_user)` 有两个真问题：
      ① 漏加一条就是一个**未鉴权的后门**，而且很难发现；
      ② 新增路由时容易忘记加，安全性靠"记得"是不可靠的。
    改为**默认全保护 + 显式放行清单**：
      只有下面 PUBLIC 里列出的路由不需要登录，其余一律要求登录。
      ★ 新增业务路由默认就是受保护的 —— 安全的默认值。

═══════════════════════════════════════════════════════════════════════
【明确放行（AUTH_SPEC §6）】
═══════════════════════════════════════════════════════════════════════
    /health                  健康检查（运维探活，不该需要账号）
    /auth/login              登录本身
    ★ /docs 与 /openapi.json 在 main.py 里，不受本 router 影响。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.api.routes import (
    ai,
    ai_draft,
    analytics,
    auth,
    business_insight,
    connections,
    contents,
    customer_workspace,
    customers,
    dashboard,
    dashboard_ai,
    handover,
    health,
    imports,
    knowledge,
    messages,
    opportunities,
    profile,
    projects,
    proposals,
    reports,
    research,
    settings as settings_routes,
    researches,
    risks,
    visits,
)

#: 公开路由：不需要登录
public_router = APIRouter()
public_router.include_router(health.router)
public_router.include_router(auth.router)

#: 受保护路由：★ 整个 router 统一挂鉴权依赖，所有子路由自动受保护
protected_router = APIRouter(dependencies=[Depends(get_current_user)])
protected_router.include_router(imports.router)
protected_router.include_router(customers.router)
protected_router.include_router(customer_workspace.router)
protected_router.include_router(handover.router)
protected_router.include_router(reports.router)
protected_router.include_router(messages.router)
protected_router.include_router(ai.router)
protected_router.include_router(dashboard.router)
protected_router.include_router(dashboard_ai.router)
protected_router.include_router(risks.router)
protected_router.include_router(visits.router)
protected_router.include_router(research.router)
protected_router.include_router(researches.router)
protected_router.include_router(knowledge.router)
protected_router.include_router(proposals.router)
protected_router.include_router(business_insight.router)
protected_router.include_router(settings_routes.router)
protected_router.include_router(profile.router)
protected_router.include_router(ai_draft.router)
protected_router.include_router(analytics.router)
protected_router.include_router(opportunities.router)
protected_router.include_router(projects.router)
protected_router.include_router(contents.router)
protected_router.include_router(connections.router)

#: main.py 仍然只 import 这一个名字，避免改多处
api_router = APIRouter()
api_router.include_router(public_router)
api_router.include_router(protected_router)

__all__ = ["api_router", "protected_router", "public_router"]
