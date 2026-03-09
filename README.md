# Language Check-in（语言训练打卡站）

一个面向日常英语训练的小型 Web 应用：
- 白名单手机号登录
- 每日 5 项任务打卡
- 今日听力素材（YouTube 内嵌播放 + 全屏）
- 句型卡 / 强制开口 / 录音上传
- 历史回顾 + 周报统计
- 个人资料（昵称 / slogan / 头像 / 主题色）
- 每晚 23:50 预拉取、次日 00:10 自动切换素材

---

## 1. 功能概览

### 每日训练
- 听力输入（自动推荐）
- 句型卡练习（9 个高频句，AI 生成）
- 强制开口（支持录音上传）
- 对话实战
- 错误复盘

### 素材更新机制（两阶段）
- 每晚 **23:50**：预拉取次日素材到数据库表 `material_prefetch`
- 次日 **00:10**：从数据库预拉取表切换到 `daily_status`
- 若 23:50 预拉取失败，00:10 会自动走兜底生成

### 打卡与统计
- 必须 5/5 任务完成后可打卡
- 可查看历史记录
- 周报展示打卡率与热力图

### 听力播放器
- 主页面内嵌 YouTube 视频播放
- 支持全屏播放
- 提供“在 YouTube 打开”备用入口
- 已修复：切换“往期 / 今天”时播放器丢失问题

---

## 2. 技术栈

- Python 3
- Flask
- SQLite
- Gunicorn
- 原生 HTML / CSS / JS 模板

---

## 3. 公开仓库说明（脱敏版）

这个仓库默认只保留：
- 前端 / 后端源码
- 脱敏后的公开演示数据库：`public_demo/checkin.public.db`
- 脱敏后的公开演示录音：`recordings/public/`
- 脱敏后的公开演示头像：`static/public-avatars/`

**不会提交到公开仓库的内容：**
- 真实生产数据库 `checkin.db`
- 真实录音、真实头像
- `.secret_key`、环境密钥、会话密钥
- 本地日志、缓存、虚拟环境

---

## 4. 项目结构

```text
language-checkin/
├── app.py
├── language-checkin.service
├── prefetch_0010.py
├── refresh_0010.py
├── reminder.py
├── remind_lulu_email.py
├── requirements.txt
├── .gitignore
├── templates/
│   ├── login.html
│   ├── index.html
│   └── weekly.html
├── static/
│   └── public-avatars/
├── recordings/
│   └── public/
├── public_demo/
│   ├── checkin.public.db
│   └── manifest.json
└── scripts/
    └── export_public_demo.py
```

---

## 5. 本地运行

### 方式 A：用公开脱敏数据直接跑 Demo

```bash
cd apps/language-checkin
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp public_demo/checkin.public.db checkin.db
python app.py
```

默认监听：`127.0.0.1:8099`

### 方式 B：使用你自己的生产数据库

把自己的 `checkin.db` 放到项目根目录，再启动：

```bash
python app.py
```

---

## 6. 生产运行（Gunicorn 示例）

```bash
cd apps/language-checkin
source .venv/bin/activate
gunicorn -w 2 -b 127.0.0.1:8099 app:app
```

如需对外访问，建议前置 Nginx 反向代理并配置 HTTPS。

---

## 7. 关键接口（摘要）

- `GET /`：主页
- `GET /login` / `POST /login` / `GET /logout`
- `GET /weekly`
- `GET /api/today`
- `GET /api/history`
- `GET /api/day/<day>`
- `POST /api/task/<task_key>/toggle`
- `POST /api/checkin`
- `POST /api/refresh-materials`
- `POST /api/profile`
- `POST /api/profile/avatar`
- `POST /api/recordings/upload`
- `GET /recordings/<path>`

---

## 8. 公开 Demo 数据刷新

如果你在私有环境中更新了真实数据，想重新生成一份可公开上传的脱敏包：

```bash
cd apps/language-checkin
python3 scripts/export_public_demo.py
```

生成结果：
- `public_demo/checkin.public.db`
- `public_demo/manifest.json`
- `recordings/public/*.wav`
- `static/public-avatars/*.svg`

---

## 9. 提醒脚本配置

`remind_lulu_email.py` 默认使用占位配置；上线时请通过环境变量注入真实值，例如：

- `LULU_EMAIL`
- `LULU_NICKNAME`
- `GOG_ACCOUNT`
- `OWNER_TELEGRAM_ID`
- `LANGUAGE_CHECKIN_SITE_URL`

---

## 10. 当前同步状态

- 已同步前端 / 后端源码
- 已同步脱敏版公开数据库与演示素材
- 已移除公开仓库中的运行时缓存、虚拟环境和本地密钥（当前版本不再跟踪）
