# Football 项目代码优化报告

## 总体评价

项目整体架构设计已相当规范，分层清晰（数据层 → 业务层 → UI层），遵循了单一职责原则。优化集中在 **5个具体问题**，均有明确的 Bug 风险或运行时隐患。

---

## 问题一：硬编码 Windows 绝对路径（严重 ⚠️）

**文件**：`app/config.py` → `DatabaseConfig`

**原代码**：
```python
path: str = field(
    default_factory=lambda: os.getenv(
        "LOTTERY_DB_PATH",
        r"C:\Users\13559\Desktop\2026\pyproject\Football\data\lottery_data.db"
    )
)
```

**问题**：默认路径为 Windows 绝对路径，在 Linux / macOS 或任何非该具体机器上运行时直接报 `FileNotFoundError`，是跨平台部署的硬性障碍。

**修复**：
```python
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

path: str = field(
    default_factory=lambda: os.getenv(
        "LOTTERY_DB_PATH",
        str(_PROJECT_ROOT / "data" / "lottery_data.db"),
    )
)
```

**效果**：使用 `pathlib` 从 `config.py` 所在位置向上推导项目根目录，自动适配 Windows / Linux / macOS，无需任何环境变量即可开箱即用。

---

## 问题二：数据库连接未使用上下文管理器（中等 ⚠️）

**文件**：`app/data_service.py` → `load_and_process_data`

**原代码**：
```python
conn = _connect_db(DB_CONFIG.path)
try:
    raw_df = _fetch_raw_records(conn, DB_CONFIG.table_name)
finally:
    conn.close()
```

**问题**：手动 `try/finally` 是低级的资源管理方式，在嵌套异常场景下容易遗漏 `close()`。同时 `_connect_db` 函数被其他层直接使用时无法保证关闭。

**修复**：将 `_connect_db` 改为 `@contextmanager`：
```python
@contextmanager
def _db_connection(db_path: str) -> Generator[sqlite3.Connection, None, None]:
    if not os.path.exists(db_path):
        raise FileNotFoundError(...)
    conn = sqlite3.connect(db_path)
    try:
        yield conn
    finally:
        conn.close()

# 调用方
with _db_connection(DB_CONFIG.path) as conn:
    raw_df = _fetch_raw_records(conn, DB_CONFIG.table_name)
```

**效果**：资源管理更安全，代码更简洁，符合 Python 惯用语（Pythonic）。

---

## 问题三：`storage.py` 存在死代码（低 🔧）

**文件**：`crawler/storage.py`

**原代码**：文件中同时存在两个函数：
- `_create_engine_and_tables()`（包含一段错误代码 `conn.execute(conn.connection.cursor().__class__.__module__ and ...)`，实际上是个无效表达式）
- `_init_engine()`（功能正确，实际被调用的版本）

**问题**：`_create_engine_and_tables` 是一个未被调用的废弃函数，其中还有一行语法上合法但逻辑错误的代码（利用短路求值来绕过 import），是典型的遗留代码噪声，会误导维护者。

**修复**：删除 `_create_engine_and_tables`，保留并整理 `_init_engine`，同时将 `get_latest_match_date` 中的兜底日期从硬编码的 `"2026-05-24"` 改为合理默认值 `"2020-01-01"`。

---

## 问题四：Matplotlib Figure 内存泄漏（中等 ⚠️）

**文件**：`app/ui/app.py`

**原代码**：
```python
fig = plot_001_result_distribution(df_chart_source)
st.pyplot(fig)
# ← 没有 plt.close(fig)
```

**问题**：Streamlit 每次用户交互都会触发脚本 rerun。每次 rerun 都会创建新的 `Figure` 对象，但旧的对象不会被 GC 回收（Matplotlib 内部保持引用）。长时间运行后内存占用持续增长，在 5 个 Tab × 频繁交互的场景下尤为明显。

**修复**：封装 `_show_fig` 辅助函数：
```python
def _show_fig(fig) -> None:
    """渲染 Figure 并立即关闭，防止跨 rerun 的内存泄漏。"""
    st.pyplot(fig)
    plt.close(fig)
```

全部 `st.pyplot(fig)` 调用替换为 `_show_fig(fig)`。

同时在 `charts.py` 顶部添加：
```python
matplotlib.use("Agg")  # 非交互后端，防止多线程 GUI 冲突
```

---

## 问题五：Pandas groupby 兼容性警告（低 🔧）

**文件**：`app/analytics.py` → `calc_underdog_trend`

**原代码**：
```python
trend = (
    df_streak
    .groupby("比赛月份")
    .apply(_rate, include_groups=False)  # pandas 2.2+ DeprecationWarning
    .reset_index()
)
```

**问题**：`include_groups` 参数在 pandas 2.2 中引入但同时标记为 Deprecated，在 pandas 2.x 的某些版本会产生 `DeprecationWarning`，未来版本将移除。

**修复**：改用更明确的 `agg` 方式：
```python
monthly = df_streak.groupby("比赛月份")["盘路结果"].agg(
    total="count",
    underdog=lambda s: (s == DOMAIN.label_underdog).sum(),
).reset_index()
monthly["下盘率"] = monthly["underdog"] / monthly["total"] * 100
```

---

## 其他规范化改动

| 位置 | 改动 | 说明 |
|------|------|------|
| `app/ui/app.py` | `width='stretch'` → `use_container_width=True` | `width='stretch'` 是非标准参数，官方推荐写法 |
| `crawler/storage.py` | `get_latest_match_date` 兜底值 `"2026-05-24"` → `"2020-01-01"` | 硬编码具体日期会在该日期之前的数据上产生错误的增量起点 |
| `app/data_service.py` | `_add_single_flag` 中 `truthy` 集合提前计算 | 原代码在 `apply` 的每一行都重新生成 `{str(v) for v in ...}`，改为提前计算节省重复开销 |
| `crawler/storage.py` | 统一 `from sqlalchemy import func, text` 的 import 位置 | 原代码在函数内部 import，移至文件顶部符合 PEP8 规范 |

---

## 优化文件汇总

```
optimized/
├── app/
│   ├── config.py          ✅ 修复硬编码路径
│   ├── data_service.py    ✅ 使用 contextmanager 管理连接
│   ├── analytics.py       ✅ 修复 pandas groupby 兼容性
│   └── ui/
│       ├── app.py         ✅ 添加 plt.close，修复内存泄漏
│       └── charts.py      ✅ 添加 matplotlib.use("Agg")
└── crawler/
    └── storage.py         ✅ 删除死代码，修复兜底日期
```

未修改的文件（逻辑无问题）：
- `app/ui/components.py`
- `app/ui/styles.py`
- `crawler/config.py`
- `crawler/models.py`
- `crawler/scheduler.py`
- `crawler/spider.py`
