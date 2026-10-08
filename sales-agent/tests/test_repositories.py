"""tests/test_repositories.py · TASK-002 Scope A 验收测试（Repository 抽象）。

════════════════════════════════════════════════════════════════════════
【本文件的验证思路 —— 为什么这样测才算数】
════════════════════════════════════════════════════════════════════════
被测：`app/repositories/`（base 接口 + json_store 底座 + json_repo 实现）以及
重构后的 `app/state.py`（改成走 Repository）。

重构最怕的是"看着没变、其实悄悄变了"：路径换了、schema_version 丢了、
新的在前变成旧的在先、状态没变也刷盘……本文件专门钉这些点：

  ① **接口一致性**：三个 Json 实现必须真的是 base 里那三个抽象接口的实例，
     且接口声明的每个方法都实现了（将来加接口方法忘了实现 → 这里立刻红）。
  ② **读写往返**：写进去 → 读出来，字段一字不差；文件里确实带 `schema_version`。
  ③ **与旧 state 行为等价**：拿同一批操作分别走 `state.*`（对外 API）与
     `repositories.*`（新抽象），比**落盘文件的字节内容**是否一致 —— 这比"返回对象相等"更硬，
     因为它同时钉住了格式与顺序。**顺序**（新的在前）单独再钉一遍。
  ④ **没改既有语义**：分页切片、`list_task_runs` 的 offset/limit、
     `run_stats_by_task` 只认带 task_id 的记录、`mark_task_has_run` 幂等且**状态没变不写盘**。
  ⑤ **错误不静默**：坏 JSON 必须抛 StateError（执行记录是审计凭据，不许假装没记录）。
  ⑥ **多进程边界没被放松**：唯一写入路径仍是 json_store 的原子写（os.replace）——
     断言真实现里用的是 json_store，而不是自己偷偷开文件写。

隔离：只用 `tmp_path`，绝不碰仓库的 state/、data/uploads/、outputs/。

依据：`docs/施工指令-TASK-002-UI骨架.md` Scope A、`decisions/004-upgrade-gate.md` 第 2 条。
"""

from __future__ import annotations

import json
import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402

from app import repositories, state  # noqa: E402
from app.repositories import json_store  # noqa: E402
from app.repositories.base import (  # noqa: E402
    REPOSITORY_INTERFACES,
    ExecutionRepository,
    TaskRepository,
    UploadRepository,
)


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """三个目录都指到本次测试的临时目录（仓库里的真实 state/ 一个字节都不碰）。"""
    monkeypatch.setenv("SRA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SRA_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SRA_OUTPUT_DIR", str(tmp_path / "outputs"))
    return tmp_path


# ════════════════════════════════════════════════════════════════════════
# ① 接口一致性（新增接口方法忘了实现 → 这里红）
# ════════════════════════════════════════════════════════════════════════
def test_三个实现都是对应抽象接口的实例():
    assert isinstance(repositories.uploads(), UploadRepository)
    assert isinstance(repositories.tasks(), TaskRepository)
    assert isinstance(repositories.executions(), ExecutionRepository)


def test_抽象接口不能被直接实例化():
    for interface in REPOSITORY_INTERFACES:
        with pytest.raises(TypeError):
            interface()  # type: ignore[abstract]


def test_接口声明的每个抽象方法都实现了():
    pairs = [
        (UploadRepository, repositories.uploads()),
        (TaskRepository, repositories.tasks()),
        (ExecutionRepository, repositories.executions()),
    ]
    for interface, impl in pairs:
        for name in interface.__abstractmethods__:
            assert callable(getattr(impl, name, None)), f"{type(impl).__name__} 缺 {name}"


def test_三个实现分别落在三个不同的文件上():
    paths = {
        repositories.uploads().path().name,
        repositories.tasks().path().name,
        repositories.executions().path().name,
    }
    assert paths == {"uploads.json", "tasks.json", "executions.json"}


def test_默认实例是惰性单例():
    assert repositories.tasks() is repositories.tasks()
    assert repositories.uploads() is repositories.uploads()
    assert repositories.executions() is repositories.executions()


def test_工厂能造出互不干扰的独立实例(tmp_path):
    """TASK-009 换 SQLite 时要靠这个：实现可替换，接口不变。"""
    custom = tmp_path / "custom_tasks.json"
    repo = repositories.build_task_repository(path_getter=lambda: custom)
    assert isinstance(repo, TaskRepository)
    repo.add({"task_id": "t_x", "status": "created"})
    assert custom.exists()
    assert repositories.tasks().get("t_x") is None       # 默认仓库没被污染
    assert repo.get("t_x")["task_id"] == "t_x"


# ════════════════════════════════════════════════════════════════════════
# ② 读写往返 + 落盘格式
# ════════════════════════════════════════════════════════════════════════
def test_任务读写往返字段一字不差():
    record = {
        "task_id": "t_roundtrip",
        "name": "周报",
        "status": state.TASK_STATUS_CREATED,
        "spec": {"metrics": ["sales_amount"], "time_range": {"start": "2011-11-21"}},
        "nested": {"list": [1, 2, {"深": "中文"}], "n": None},
    }
    repositories.tasks().add(record)
    got = repositories.tasks().get("t_roundtrip")
    assert got == record                                 # 不认识字段语义 → 原样进原样出
    assert repositories.tasks().get("不存在") is None


def test_上传记录读写往返():
    record = {"file_id": "f_1", "filename": "Online Retail.xlsx", "rows": 541909, "columns": 8}
    repositories.uploads().add(record)
    assert repositories.uploads().get("f_1") == record
    assert repositories.uploads().count() == 1
    assert repositories.uploads().list()[0]["filename"] == "Online Retail.xlsx"


def test_执行记录读写往返():
    record = {"execution_id": "x_1", "task_id": "t_1", "status": "success"}
    repositories.executions().add(record)
    assert repositories.executions().get("x_1") == record
    assert repositories.executions().count() == 1


def test_落盘文件带schema_version且是新的在前():
    repositories.tasks().add({"task_id": "t_first"})
    repositories.tasks().add({"task_id": "t_second"})

    payload = json.loads(repositories.tasks().path().read_text(encoding="utf-8"))
    assert payload["schema_version"] == json_store.SCHEMA_VERSION == 1
    assert isinstance(payload["tasks"], list)            # 不是裸列表，带 key
    assert [t["task_id"] for t in payload["tasks"]] == ["t_second", "t_first"]


def test_空文件读到空列表而不是报错():
    assert repositories.tasks().list() == ([], 0)        # 首次运行：文件还不存在
    assert repositories.tasks().count() == 0
    assert repositories.tasks().get("t_any") is None


# ════════════════════════════════════════════════════════════════════════
# ③ 与旧 state 行为等价（比落盘**字节**，比"返回对象相等"更硬）
# ════════════════════════════════════════════════════════════════════════
def _via_state() -> dict[str, str]:
    state.record_task(
        task_id="t_eq", name="等价", file_id="f_eq", source_filename="a.xlsx",
        data_sha256="hash", spec={"metrics": ["sales_amount"]}, data_snapshot_match=True,
    )
    state.record_execution({"execution_id": "x_eq1", "task_id": "t_eq", "status": "success"})
    state.record_execution({"execution_id": "x_eq2", "task_id": "t_eq", "status": "failed"})
    state.mark_task_has_run("t_eq")
    state.record_upload(
        file_id="f_eq", filename="a.xlsx", stored_path="p", size_bytes=1,
        sha256="hash", rows=2, column_names=["a", "b"],
    )
    return {name: path.read_text(encoding="utf-8") for name, path in _files().items()}


def _via_repositories() -> dict[str, str]:
    repositories.tasks().add({
        "task_id": "t_eq", "name": "等价", "status": state.TASK_STATUS_CREATED,
        "file_id": "f_eq", "source_filename": "a.xlsx", "data_sha256": "hash",
        "data_snapshot_match": True, "spec": {"metrics": ["sales_amount"]},
        "created_at": "2026-09-23T00:00:00+08:00", "updated_at": "2026-09-23T00:00:00+08:00",
    })
    # created_at 由 state.record_execution 补（仓库**不生成时间戳**，见 base.py 的边界说明）
    repositories.executions().add({"execution_id": "x_eq1", "task_id": "t_eq", "status": "success",
                                   "created_at": "2026-09-23T00:00:00+08:00"})
    repositories.executions().add({"execution_id": "x_eq2", "task_id": "t_eq", "status": "failed",
                                   "created_at": "2026-09-23T00:00:00+08:00"})
    repositories.tasks().set_status("t_eq", state.TASK_STATUS_HAS_RUN, "2026-09-23T00:00:00+08:00")
    repositories.uploads().add({
        "file_id": "f_eq", "filename": "a.xlsx", "stored_path": "p", "size_bytes": 1,
        "sha256": "hash", "rows": 2, "columns": 2, "column_names": ["a", "b"],
        "created_at": "2026-09-23T00:00:00+08:00",
    })
    return {name: path.read_text(encoding="utf-8") for name, path in _files().items()}


def _files() -> dict[str, pathlib.Path]:
    return {
        "uploads": state.uploads_file(),
        "executions": state.executions_file(),
        "tasks": state.tasks_file(),
    }


def test_state走Repository后落盘字节与手写仓储一致(monkeypatch):
    """state.py 的对外函数 = "组装记录 + 交给仓库"；把时钟钉死后，两者必须写出**同一份文件**。

    这条同时钉住了：字段顺序、新的在前、schema_version、缩进格式、时间戳从哪来。
    """
    monkeypatch.setattr(state, "now_iso", lambda: "2026-09-23T00:00:00+08:00")
    by_state = _via_state()
    for path in _files().values():
        path.unlink()                                     # 清空重来
    by_repo = _via_repositories()

    for name in ("uploads", "executions", "tasks"):
        assert by_state[name] == by_repo[name], f"{name}.json 落盘内容不一致"


def test_state对外函数读得到Repository写的数据():
    repositories.tasks().add({"task_id": "t_cross", "status": "created"})
    assert state.get_task("t_cross")["task_id"] == "t_cross"
    assert state.count_tasks() == 1
    assert state.list_tasks()[1] == 1


# ════════════════════════════════════════════════════════════════════════
# ④ 既有语义没变（顺序 / 分页 / 统计 / 幂等）
# ════════════════════════════════════════════════════════════════════════
def test_任务列表分页并返回总数():
    for i in range(5):
        repositories.tasks().add({"task_id": f"t_{i}", "status": "created"})
    page, total = repositories.tasks().list(limit=2, offset=1)
    assert total == 5
    assert [t["task_id"] for t in page] == ["t_3", "t_2"]   # 新的在前，从第 1 条开始切
    assert state.list_tasks(limit=2, offset=1)[1] == 5


def test_按任务筛执行记录并分页():
    for i in range(4):
        repositories.executions().add({"execution_id": f"x_{i}", "task_id": "t_a", "status": "success"})
    repositories.executions().add({"execution_id": "x_other", "task_id": "t_b", "status": "success"})

    page, total = repositories.executions().list_by_task("t_a", limit=2, offset=1)
    assert total == 4                                      # 只算 t_a 的
    assert [r["execution_id"] for r in page] == ["x_2", "x_1"]
    assert repositories.executions().list_by_task("t_missing") == ([], 0)


def test_run_stats只认带task_id的记录且取最近一次():
    repositories.executions().add({"execution_id": "x_ad_hoc", "status": "success"})   # 无 task_id
    repositories.executions().add({"execution_id": "x_1", "task_id": "t_a", "status": "success",
                                   "created_at": "2026-09-23T10:00:00+08:00"})
    repositories.executions().add({"execution_id": "x_2", "task_id": "t_a", "status": "failed",
                                   "created_at": "2026-09-23T11:00:00+08:00"})

    stats = repositories.executions().task_run_stats()
    assert set(stats) == {"t_a"}                           # ad-hoc 不计入任何任务
    assert stats["t_a"] == {
        "run_count": 2,
        "last_run_at": "2026-09-23T11:00:00+08:00",        # 新的在前 → 第一条即最近
        "last_run_status": "failed",
    }
    assert state.run_stats_by_task() == stats


def test_mark_task_has_run幂等且状态没变不写盘():
    repositories.tasks().add({"task_id": "t_idem", "status": state.TASK_STATUS_CREATED,
                              "updated_at": "2026-09-23T00:00:00+08:00"})
    path = state.tasks_file()
    before = path.stat().st_mtime_ns

    first = state.mark_task_has_run("t_idem")
    assert first["status"] == state.TASK_STATUS_HAS_RUN
    assert first["updated_at"] != "2026-09-23T00:00:00+08:00"    # 第一次真改了 → 刷 updated_at

    stamp = path.stat().st_mtime_ns
    second = state.mark_task_has_run("t_idem")
    assert second["status"] == state.TASK_STATUS_HAS_RUN
    assert path.stat().st_mtime_ns == stamp                     # 第二次没改 → 不写盘（幂等）
    assert stamp >= before


def test_mark_task_has_run对不存在的任务返回None():
    assert state.mark_task_has_run("t_不存在") is None
    repositories.tasks().add({"task_id": "t_x"})
    assert state.mark_task_has_run("t_不存在") is None


def test_state_summary字段与旧版一致():
    repositories.uploads().add({"file_id": "f_1"})
    repositories.tasks().add({"task_id": "t_1"})
    repositories.executions().add({"execution_id": "x_1"})
    summary = state.state_summary()
    assert summary == {
        "state_dir": str(state.state_dir()),
        "schema_version": 1,
        "uploads": 1,
        "executions": 1,
        "tasks": 1,
        "readable": True,
    }


def test_坏JSON抛StateError而不是当没记录():
    path = state.tasks_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ 这不是 JSON", encoding="utf-8")

    with pytest.raises(state.StateError):
        state.list_tasks()
    with pytest.raises(json_store.StateError):
        repositories.tasks().count()
    assert state.state_summary()["readable"] is False       # 健康检查不抛错，但要如实说读不了


# ════════════════════════════════════════════════════════════════════════
# ⑤ 边界：只走 json_store 的原子写（多进程边界没被放松）
# ════════════════════════════════════════════════════════════════════════
def test_所有写入都走json_store的原子写(monkeypatch):
    calls = {"write": 0}
    real_write = json_store.write_records

    def counting_write(path, key, records):
        calls["write"] += 1
        return real_write(path, key, records)

    monkeypatch.setattr(json_store, "write_records", counting_write)
    repositories.tasks().add({"task_id": "t_1"})
    repositories.tasks().set_status("t_1", "has_run", "2026-09-23T00:00:00+08:00")
    assert calls["write"] == 2                              # 一次 add、一次真改状态
    repositories.tasks().set_status("t_1", "has_run", "2026-09-23T01:00:00+08:00")
    assert calls["write"] == 2                              # 状态没变 → 不写


def test_写盘不留下临时文件():
    repositories.tasks().add({"task_id": "t_1"})
    leftovers = list(state.state_dir().glob("*.tmp"))
    assert leftovers == []                                  # os.replace 之后临时文件必须没了


def test_裸列表形态的旧文件也能读():
    """手工编辑过的历史文件（顶层是裸列表）不能读崩 —— 兼容逻辑从旧 state.py 原样保留。"""
    path = state.tasks_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([{"task_id": "t_legacy"}], ensure_ascii=False), encoding="utf-8")
    assert repositories.tasks().get("t_legacy")["task_id"] == "t_legacy"
