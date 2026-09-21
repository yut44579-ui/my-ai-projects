# 施工指令 · TASK-001-FIX（修测试集日期 + 初始化 Git）

## TASK ID：TASK-001-FIX
**任务名称**：修正测试集日期越界 + 初始化 Git 仓库

## 目标
1. 修正 `tests/test_cases.json` 中 16 条用例的日期越界问题
2. 初始化 Git 仓库并完成首次提交

## 背景
TASK-001（写测试集）产物已通过大部分验收：50 条、类型分布 16/16/18、split dev40/holdout10 均正确。
但验收发现一处问题：**normal 类的"上周"全部写成 2011-12-05 ~ 2011-12-11**，而数据集 `data/Online Retail.xlsx` 只到 **2011-12-09**（周五）——末尾周数据不完整，会导致生成的周报是残缺的。

（`e01` 的 `2012-01-01~2012-01-31` 是**故意**测试"请求超出数据范围"的 edge 用例，**不要改它**。）

## 相关文件
- `tests/test_cases.json`（要改）
- `docs/TASKS.md`（改完把 TASK-001 标记 ✅，并加一行记录 TASK-001-FIX）
- `.gitignore`（新建）

## 技术要求
1. 把越界用例的"上周"改到**有完整数据的周**，推荐用 **2011-11-21 ~ 2011-11-27**（周一到周日，数据完整）；
   若某条用例的语义要求"最近一周"，可改用 **2011-11-28 ~ 2011-12-04**（同样完整）。
2. 每条改动后，`expected.time_range` 必须与改后的日期一致。
3. 保持总数 50、类型分布 16/16/18、split dev40/holdout10 **完全不变**。
4. Git：
   - `git init`（在 `D:\sales-report-agent`）
   - 新建 `.gitignore`，内容：
     ```
     .venv/
     __pycache__/
     *.pyc
     outputs/
     .pytest_cache/
     ```
   - `git add -A && git commit -m "test: add 50 test cases for sales report agent"`
   - 如需配置身份：`git -c user.name="..." -c user.email="..."`（用 `hermes-dev / dev@local` 即可）

## 禁止事项
- 不要改 edge / needs_clarification 类用例（除日期检查外）
- 不要改 `e01` 的 2012 年日期（故意的）
- 不要改 `CLAUDE.md` / `docs/*.md`（除 TASKS.md 的状态更新）
- 不要写业务代码（engine/ 等是下一个 TASK）
- 不要加依赖、不要重装 venv

## 验收标准
1. 越界检查：用例中出现的日期**没有任何一条 > 2011-12-09**（除 e01 故意的 2012 年）
2. 总数仍为 50；类型分布仍为 16/16/18；split 仍为 dev40/holdout10
3. JSON 合法
4. `git log --oneline` 至少有 1 条提交；`git status` 干净
5. `docs/TASKS.md` 中 TASK-001 状态改为 ✅

## 测试要求（完成后贴出实际命令输出）
```bash
cd /d/sales-report-agent
.venv\Scripts\python.exe -c "import json,re; d=json.load(open('tests/test_cases.json',encoding='utf-8')); cs=d['cases']; from collections import Counter; print('总数',len(cs)); print(Counter(c['type'] for c in cs)); print(Counter(c['split'] for c in cs)); bad=[(c['id'],m) for c in cs for m in re.findall(r'20\d\d-\d\d-\d\d',json.dumps(c,ensure_ascii=False)) if m>'2011-12-09' and not m.startswith('2012')]; print('越界',bad)"
git log --oneline
git status --short
```

## 汇报格式
```
【完成任务】TASK-001-FIX
【改动文件】列出
【验证结果】越界数 / 分布 / git log / git status（贴实际输出）
【遗留】无则写"无"
```
