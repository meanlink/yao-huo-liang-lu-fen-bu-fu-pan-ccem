# 窑火良率分布复盘

描述统计与稳健分布的真实业务分析小型项目。完整模拟客户、痛点、上下游、算法公式与一天工作剧本见 [BUSINESS.md](BUSINESS.md)。

## 模块与业务流程
数据接入（JSON/CSV、字段验证、批次指纹）→ 参数化分析（异步 Python worker）→ 结果复核（指标、图形、明细、解释、批准）→ 定时分发（本地收件箱、CSV/JSON、SHA256）；另有业务总览和审计轨迹。
三个贯穿全栈的实体：Dataset 数据批次、Job 分析任务、Report 分发计划；辅助 User、Audit、Delivery。

### 外协窑对账复盘
外协窑只报烧成件数与四舍五入到一位小数的良率，整批合格件数由厂内总账后补。新增的 `reconciliations` 模块与之对应：

1. **复盘试算**：一位小数良率反推该窑可能的整数合格件数区间（如 2000 件、93.0% → 1859–1860 件），并对照目标良率给出可能的单窑结论。总账未到时不允许批准。
2. **总账对账**：录入整批合格件数，用“各窑区间之和必须等于总账”收窄每一窑的上下限；区间整体高于/低于达标线的窑次直接判定，跨越达标线的列为待核。
3. **无解解释**：总账低于各窑最少合计或高于最多合计时，明确给出差额多少件以及应核对的对象，不做任何单窑推断。
4. **后补实数**：补报任一窑实际合格件数后，该窑按实数判定，其余窑重新受整批约束；若补报值与上报良率不一致、超过烧成件数或使总账对不上，均列为待核原因。
5. **批准与留档**：只有总账有解且每个窑次都能确定是否达标时才可批准；批准携带版本号，补报与改账都会递增版本，过期批准被服务端拒绝（409）。每次批准在 `reconciliation_approvals` 中保留当时的完整依据快照，后续改动只标记“旧批准过期”，不覆盖历史。

页面 `#/reconciliations` 可逐窑核对上报区间、整批约束后区间、达标线与待核原因，并提供补报和批准入口。

## 快速启动
```bash
cp .env.example .env
docker compose up -d --build --wait
docker compose exec -T backend python -m scripts.seed_demo
```
访问 http://localhost:18200；后端文档 http://localhost:19200/docs；数据库宿主端口 17700。
`SEED_DEMO=true` 自动幂等 seed；不覆盖用户修改。手动 seed 命令可重复执行。

## 演示账号与权限
|账号|密码|真实权限|
|---|---|---|
|admin@demo.local|Demo123!|接入、执行、批准（含对账批准）、分发、暂停、读取与下载|
|analyst@demo.local|Demo123!|接入、执行、对账录入与补报、读取与下载；不能批准或安排分发|
|viewer@demo.local|Demo123!|只读与下载|

## 技术与本地开发
Python 3.12 / FastAPI / SQLAlchemy / NumPy / SciPy / scikit-learn；PostgreSQL 16；Redis 7；Nginx；原生 HTML CSS JavaScript ES modules（用户指定）。后端所有校验、持久化和算法在服务端。无前端打包步骤。
后端：安装 backend/requirements.txt，设置 DATABASE_URL、REDIS_URL、JWT_SECRET、ARTIFACT_DIR 后在 backend/ 执行 `python -m migrations.initial`、`python -m scripts.seed_demo`、`uvicorn src.main:app`，另开 `python -m src.worker`。前端使用带 /api 反向代理的 Nginx，直接打开 index.html 不支持 API。

## 目录与工程约定
backend/src/{models,schemas,services,routes,controllers,middlewares,constants,config,utils}；backend/scripts/seed_demo.py；backend/migrations/initial.py。
frontend/src/{api,stores,types,components/common,hooks,pages,router,utils,constants}。
Role 枚举：admin/analyst/viewer；JobStatus：queued/running/completed/failed。后端 backend/src/constants/enums.py，前端 frontend/src/constants/enums.js；路由权限在 middlewares/auth.py；页面按相同角色控制操作入口。

## 环境变量与 Docker
COMPOSE_PROJECT_NAME=kiln-yield；FRONTEND_PORT=18200；BACKEND_PORT=19200；DB_PORT=17700；DB_PASSWORD、JWT_SECRET 见 .env.example；SEED_DEMO 默认 true。共享命名产物卷供后端下载与 worker 写入，数据库/Redis 各持久卷。
恰好五服务均有健康检查。Redis 队列加 SQL 排队状态兜底；worker 按 next_run 生成本地收件箱文件。周期 60 秒至 30 天。分发结果为批准分析的固定快照；若需新数据重新计算并建立新计划。当前不连接邮件或企业聊天。
错误响应、请求编号、Redis 每 IP 每分钟 240 次限流、JWT 和数据库操作审计。演示密钥与密码仅供本机模拟；生产部署前配置独立密钥与账号。

## 数据与验收
所有数据为固定随机种子生成的模拟业务数据，不能作为实际生产决策证据。项目独立算法、输入边界及业务局限详见 BUSINESS.md。测试与截图统一存放 ../../test-artifacts/gb-1001/，源码不含日志/截图/测试报告。
后端测试：`cd backend && python -m pytest tests -q`（对账区间数学、试算/对账/无解、补报、版本并发、批准留档与权限）。
清理本项目演示数据：`docker compose down -v --remove-orphans`（会删除本项目命名卷）。

## License
MIT，见 LICENSE。
