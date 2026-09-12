# 竞彩足球分析系统

> Streamlit → FastAPI + React + ECharts 重构版本

本项目实现对竞彩足球比赛数据的采集、存储、分析与可视化。系统正在从 Streamlit 单体架构向"前后端分离 + Docker 部署"的现代化架构迁移。

## 项目结构

```
football/
├── app/                  # 旧 Streamlit 应用（重构完成后将删除）
├── backend/              # 【规划中】FastAPI 后端
├── frontend/             # ✅ React + Vite + TypeScript 前端（已初始化）
├── crawler/              # 数据采集爬虫（保持不变）
├── data/                 # SQLite 数据库
├── logs/                 # 运行时日志
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
| 新后端 | FastAPI + SQLAlchemy + Pydantic |
| 新前端 | React 18 · TypeScript · Vite · ECharts · Zustand |
| 部署 | Docker Compose + Nginx |

## 数据流

```
[ 赛果网站 ] → [ crawler/ 爬虫 ] → [ SQLite data/lottery_data.db ] 
                                              ↓
                              ┌───────────────┴───────────────┐
                              ↓                               ↓
                  [ 旧：app/ Streamlit ]       [ 新：FastAPI backend ]
                                                          ↓
                                                  [ React frontend ]
```
