# Language Check-in（语言训练打卡站）

一个面向日常英语训练的小型 Web 应用：
- 白名单手机号登录
- 每日 5 项任务打卡
- 今日听力素材（YouTube 内嵌播放 + 全屏）
- 句型卡 / 强制开口 / 录音上传
- 昵称显示（替代手机号展示）
- 历史回顾 + 周报统计

---

## 1. 功能概览

### 每日训练
- 听力输入（自动推荐）
- 句型卡练习（9 个高频句，AI 生成）
- 强制开口（支持录音上传）
- 对话实战
- 错误复盘

### 素材更新机制（两阶段）
- 每晚 **23:50**：预拉取次日素材到数据库表 `material_prefetch`（听力 + 句型卡 + 强制开口）
  - 句型卡在此阶段通过 OpenClaw Agent 调用模型实时生成（非模板池抽取）
- 次日 **00:10**：从数据库预拉取表切换到 `daily_status`，统一替换当日旧素材
- 若 23:50 预拉取失败，00:10 会自动走兜底生成，保证当日可用

### 打卡与统计
- 必须 5/5 任务完成后可打卡
- 可查看历史天记录
- 周报展示打卡率与热力图

### 成员与展示
- 账号仍按手机号登录
- 页面展示优先显示昵称（可在“个人设置”中修改）

### 听力播放器
- 主页面内嵌 YouTube 视频播放
- 支持全屏播放
- 提供“在 YouTube 打开”备用入口

---

## 2. 技术栈

- Python 3
- Flask
- SQLite（`checkin.db`）
- Gunicorn（生产进程）
- 原生 HTML/CSS/JS 模板

---

## 3. 项目结构

```text
language-checkin/
├── app.py                    # 主应用（Flask 路由 + 业务逻辑）
├── checkin.db                # SQLite 数据库
├── templates/
│   ├── login.html            # 登录页
│   ├── index.html            # 主页面
│   └── weekly.html           # 周报页
├── recordings/               # 录音文件目录
├── requirements.txt
├── seed_today.py             # 手动生成/刷新今日素材脚本（如有）
├── reminder.py               # 提醒脚本（如有）
└── language-checkin.service  # systemd 服务配置样例
```

---

## 4. 本地运行

```bash
cd apps/language-checkin
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

默认监听：`127.0.0.1:8099`

---

## 5. 生产运行（Gunicorn 示例）

```bash
cd apps/language-checkin
source .venv/bin/activate
gunicorn -w 2 -b 127.0.0.1:8099 app:app
```

如需对外访问，建议前置 Nginx 反向代理并配置 HTTPS。

---

## 6. 关键接口（摘要）

- `GET /`：主页
- `GET /login` / `POST /login` / `GET /logout`
- `GET /weekly`：周报
- `GET /api/today`
- `GET /api/history`
- `GET /api/day/<day>`
- `POST /api/task/<task_key>/toggle`
- `POST /api/checkin`
- `POST /api/refresh-materials`
- `POST /api/profile`（昵称更新）
- `POST /api/recordings/upload`
- `GET /recordings/<path>`

---

## 7. 数据说明

- 数据库存储在 `checkin.db`
- 录音文件存储在 `recordings/`
- 应用会使用本地会话与配置文件（按 `app.py` 中实现）

---

## 8. 维护建议

- 定期备份：`checkin.db` 与 `recordings/`
- 若要公开仓库，建议避免提交敏感配置文件
- 升级样式/交互后建议先做版本备份再上线

---

## 9. 当前版本说明（本次同步）

- UI 已做柔和化排版优化（标题与卡片层级）
- 句型卡内容区已优化为垂直居中
- 听力区为站内 YouTube 播放（含全屏）
- 修复：点击任务按钮不再导致当前视频重置
- 新增：视频播放进度自动记忆（同视频续播）
