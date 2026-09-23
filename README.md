# 竞彩足球分析系统

> Streamlit → FastAPI + React + ECharts 重构版本

本项目实现对竞彩足球比赛数据的采集、存储、分析与可视化。系统正在从 Streamlit 单体架构向"前后端分离 + Docker 部署"的现代化架构迁移。

## 项目结构

```
football/
├── app/                  # Streamlit 应用（含 Redis 缓存层）
│   ├── cache.py          # 【新增】Redis 客户端 + @redis_cache 装饰器
│   ├── config.py         # 全局配置（含 RedisConfig）
│   ├── data_service.py   # 数据层（SQLite + 特征工程）
│   ├── analytics.py      # 业务分析层（calc_* 函数，已加 Redis 装饰）
│   └── ui/               # Streamlit UI 组件
├── backend/              # 【规划中】FastAPI 后端
├── frontend/             # ✅ React + Vite + TypeScript 前端（已初始化）
├── crawler/              # 数据采集爬虫（爬完自动预热 Redis）
├── data/                 # SQLite 数据库
├── logs/                 # 运行时日志
├── scripts/
│   └── precompute.py     # 【新增】Redis 预热入口（手动触发）
├── mergeDB.py            # 历史数据库合并脚本
├── sckeduler.log         # 调度器历史日志
├── streamlit.log         # Streamlit 历史日志
├── test.py               # 临时测试脚本
└── OPTIMIZATION_REPORT.md
```

## 已完成的工作

### Phase 2：前端基础设施（已完成）

- ✅ Vite 5 + React 18 + TypeScript 5 工程搭建
- ✅ ECharts 5 + echarts-for-react 图表封装
- ✅ React Router 6 路由（主仪表盘 / 趋势分析 / 赛事明细）
- ✅ Zustand 5 全局状态（日期范围、001 筛选）
- ✅ Axios 客户端（带 dev proxy 配置）
- ✅ 生产构建通过（697 modules, 1.3MB → 432KB gzip）

详细使用说明见 [`frontend/README.md`](frontend/README.md)。

### Phase X：Redis 缓存层（已完成）

> **目标**：Streamlit 用户拖滑块 / 选 001 时，6 个 `calc_*` 函数直接命中 Redis 返回（< 100ms），不再现场重算（1-3 秒）。

**实现要点：**

- `app/cache.py`：Redis 客户端 + `@redis_cache` 装饰器（懒连接 + 永久降级）
- `app/analytics.py`：6 个 `calc_*` 函数已添加 `@redis_cache`（透明启用，无需改调用方）
- `scripts/precompute.py`：爬虫完成后自动预热"常用日期窗口"（近 7/30/90 天 + 全部）
- `crawler/scheduler.py`：采集完成后自动调用 `run_precompute()`

**Redis 不可用时**：所有缓存调用静默失败，程序继续走原始 pandas 计算路径，**零破坏**。

详细使用说明见 [Redis 缓存使用](#redis-缓存使用)。

## 待办事项

参考 `Streamlit转FastAPI+React+ECharts重构_*.plan.md`：

- [ ] Phase 1：FastAPI 后端（API 层、迁移数据服务）
- [ ] Phase 2：前端基础设施（✅ 已完成）
- [ ] Phase 3：前后端联调 + 图表迁移
- [ ] Phase 4：Docker 部署

## 快速开始

### 前端开发

```bash
cd frontend
npm install
npm run dev    # 启动 Vite，访问 http://localhost:3000
```

### 前端构建

```bash
cd frontend
npm run build  # 输出到 dist/
```

## 技术栈

| 层 | 技术 |
|---|---|
| 爬虫 | Python · APScheduler · requests · BeautifulSoup · SQLAlchemy |
| 数据存储 | SQLite 3 |
| 旧 UI | Streamlit + matplotlib |
| 缓存 | Redis 5+（跨进程共享分析结果） |
| 新后端 | FastAPI + SQLAlchemy + Pydantic |
| 新前端 | React 18 · TypeScript · Vite · ECharts · Zustand |
| 部署 | Docker Compose + Nginx |

## 数据流

```
[ 赛果网站 ] → [ crawler/ 爬虫 ] → [ SQLite data/lottery_data.db ]
                                              ↓
                                ┌─────────────┴─────────────┐
                                ↓                           ↓
                [ 旧：app/ Streamlit ]        [ 新：FastAPI backend ]
                         ↓                                    ↓
                  [ Redis 缓存 ]                          [ React frontend ]
                  lottery:v{mtime}:{func}:{filter}           （已预热）
```

## Redis 缓存使用

### 安装与启动 Redis（Windows）

**方法 1：winget（推荐，Win10/11 自带）**

```powershell
winget install Redis.Redis
# 启动服务（管理员 PowerShell）
redis-server --service-start
```

**方法 2：手动下载**

1. 访问 https://github.com/microsoftarchive/redis/releases
2. 下载 `Redis-x64-*.zip`，解压到 `C:\Redis`
3. 将 `C:\Redis` 加入 PATH
4. 启动：`redis-server`

**验证**

```bash
redis-cli ping    # 应输出 PONG
```

### 安装 Python 依赖

```bash
pip install -r requirements.txt
```

### 配置（可选环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `LOTTERY_REDIS_HOST` | localhost | Redis 主机 |
| `LOTTERY_REDIS_PORT` | 6379 | Redis 端口 |
| `LOTTERY_REDIS_DB` | 0 | Redis 数据库编号 |
| `LOTTERY_REDIS_PASSWORD` | 空 | 密码（无密码留空） |
| `LOTTERY_REDIS_ENABLED` | 1 | 设为 `0` 完全关闭缓存 |
| `LOTTERY_REDIS_TIMEOUT` | 2 | 连接/读写超时（秒） |

### 自动预热（推荐）

爬虫每日采集完成后，会自动调用 `scripts/precompute.py`，把近 7/30/90 天 + 全部历史的常用指标算好写入 Redis。

```bash
# 启动调度器（每日自动采集 + 预热）
python -m crawler.scheduler
```

### 手动预热

```bash
# 默认预热（7/30/90 天 + 全部）
python scripts/precompute.py

# 自定义窗口
python scripts/precompute.py --windows 7 30 180 all
```

### 手动清空缓存

```python
from app.cache import invalidate_by_version
from data_service import get_db_mtime

mtime = get_db_mtime()
invalidate_by_version(mtime)
```

### 查看 Redis 中已缓存的 key

```bash
redis-cli keys 'lottery:*'
```

### 缓存命中率观察

启动 Streamlit 后，在终端日志中可以看到：

- `✅ Redis 命中: lottery:v1715845123.45:pan_stats:a3f2c1` → 命中
- `Redis 连接失败 ... 已永久降级到原始计算路径` → 降级中

### 缓存键命名规则

```
lottery:v{db_mtime}:{func_name}:{filter_signature}

例：
  lottery:v1715845123.45:kpi
  lottery:v1715845123.45:pan_stats:a3f2c1
  lottery:v1715845123.45:streaks:2024-01-01_2024-12-31:b9d1
```

- **db_mtime**：数据库文件修改时间。爬虫更新 SQLite 后 mtime 变 → 所有旧 key 自然失效（无需手动清理）
- **filter_signature**：过滤参数的 16 字符哈希（DataFrame 取前 200 行指纹 + 总行数 + 列名）

### TTL 默认值

| 函数 | TTL | 说明 |
|---|---|---|
| `calc_kpi` / `calc_pan_stats` | 24 小时 | 基础指标，变化少 |
| `calc_streak` / `calc_goal_stats` / `calc_underdog_trend` | 6 小时 | 带过滤的指标 |
| `calc_tracking_data` | 6 小时 | 逐场追踪 |

### 故障排查

| 现象 | 原因 | 解决 |
|---|---|---|
| 日志一直打印"Redis 连接失败 ... 永久降级" | Redis 没启动或端口不对 | `redis-cli ping` 验证；检查 `LOTTERY_REDIS_PORT` |
| 缓存不命中 | db_mtime 没读到 | 检查数据库文件路径；`LOTTERY_DB_PATH` 环境变量 |
| 拖滑块还是慢 | DataFrame 指纹变了（正常） | 这就是缓存按过滤参数区分的预期行为 |
