# GitHub 首次发布操作手册（零基础版）

这份手册用于把当前本地仓库首次发布为公开 GitHub 仓库。它只提供操作步骤，不会替你登录、创建远端仓库或公开任何内容。

配套文案见 [`github-release-content.md`](github-release-content.md)。先打开那份文档，把 `<GITHUB_USERNAME>` 换成你的真实 GitHub 用户名。

## 当前本地状态

- 本地目录：`~/Desktop/shushu-study-toolbox`
- 默认分支：`main`
- 仓库名：`shushu-study-toolbox`
- 远端：尚未配置
- GitHub CLI：本机已安装，可直接使用 `gh`
- README、MIT License、CI、示例图和发布文案：已准备

## 推荐路线

优先使用“路线 A：GitHub CLI”。它会一次完成创建仓库、添加 `origin` 和推送，步骤最少。

如果登录命令行让你不放心，使用“路线 B：网页创建空仓库 + 两条 Git 命令”。

---

## 路线 A：GitHub CLI（推荐）

### 1. 打开终端并进入项目

```bash
cd ~/Desktop/shushu-study-toolbox
```

确认位置正确：

```bash
pwd
git branch --show-current
git status --short
```

正确结果应包含：

- 路径以 `/shushu-study-toolbox` 结尾；
- 当前分支是 `main`；
- 不出现 `config.json`、`.venv/`、密码、Token 或私人素材。

### 2. 把本次宣发材料提交到本地 Git

先只暂存这次新增或修改的发布材料：

```bash
git add .gitignore \
  marketing/xiaohongshu.md \
  marketing/xiaohongshu-notion \
  docs/publish-to-github.md \
  docs/github-release-content.md
```

检查即将公开的改动：

```bash
git diff --cached --stat
git diff --cached
```

确认没有本机路径、账号隐私或不该公开的文件后再提交：

```bash
git commit -m "docs: add launch kit"
```

> 不要使用不经检查的 `git add -A`。根目录里的本地实施计划已加入 `.gitignore`，维护版计划在 `docs/` 中。

### 3. 再跑一次本地验收

使用项目虚拟环境中的 Python；如果尚未创建环境，先按 README 的安装步骤完成。

```bash
python -m pytest tests -q
python scripts/doctor.py
```

`pytest` 必须全部通过。`doctor.py` 在 Python 3.14 下可能只提示本地转写不可用；这不影响下载、来源字幕、翻译、嵌字幕、笔记和资料精读，但公开文档仍推荐 Python 3.13。

### 4. 登录 GitHub

```bash
gh auth status
```

如果显示未登录，运行：

```bash
gh auth login
```

依次选择：

1. `GitHub.com`
2. `HTTPS`
3. `Login with a web browser`

浏览器授权完成后，再运行一次 `gh auth status`，确认显示你的正确账号。

### 5. 一条命令创建公开仓库并推送

```bash
gh repo create shushu-study-toolbox \
  --public \
  --source=. \
  --remote=origin \
  --push \
  --description "本地 Agent Skill：把视频、字幕、网页和 PDF 变成双语学习材料，无需外部 API Key。"
```

这条命令会：

1. 在当前登录账号下创建公开仓库；
2. 把本地仓库设置为它的来源；
3. 添加名为 `origin` 的远端；
4. 推送当前提交。

GitHub 官方也支持这种 `--source=. --public --push` 的现有本地仓库发布方式，详见 [Adding locally hosted code to GitHub](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github) 和 [`gh repo create`](https://cli.github.com/manual/gh_repo_create)。

如果提示同名仓库已经存在，先不要改名或覆盖；打开 GitHub 确认那个仓库是不是你以前创建的，再决定使用路线 B 连接它。

### 6. 填 About 描述和 Topics

```bash
gh repo edit \
  --description "本地 Agent Skill：把视频、字幕、网页和 PDF 变成双语学习材料，无需外部 API Key。" \
  --add-topic agent-skill \
  --add-topic ai-tools \
  --add-topic bilingual \
  --add-topic claude-code \
  --add-topic codex \
  --add-topic faster-whisper \
  --add-topic ffmpeg \
  --add-topic learning-tools \
  --add-topic python \
  --add-topic srt \
  --add-topic study-tool \
  --add-topic yt-dlp
```

Topics 只能使用小写字母、数字和连字符，每个不超过 50 个字符，单仓库最多 20 个；见 GitHub 的 [Topics 规则](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics)。

### 7. 等 GitHub Actions 全绿

先找刚触发的 CI：

```bash
gh run list --workflow CI --limit 3
```

复制最新一行的 run ID，然后运行：

```bash
gh run watch <RUN_ID>
```

最终应看到 Ubuntu、macOS、Windows × Python 3.11、3.13 共 6 个任务全部为绿色 `success`。

若失败：

```bash
gh run view <RUN_ID> --log-failed
```

不要在 CI 未通过时发布小红书帖子。

### 8. 创建首个 Release

1. 打开仓库首页。
2. 点击文件列表右侧的 **Releases**。
3. 点击 **Draft a new release**。
4. 新建 Tag：`v1.0.0`，Target 选择 `main`。
5. Release title 填：`Shushu Study Toolbox v1.0.0 — 首个公开版本`。
6. 从 [`github-release-content.md`](github-release-content.md) 复制“Release 正文”。
7. 先保存为 Draft，复查后再点 **Publish release**。

GitHub 官方的 Release 流程同样建议先选择 Tag、Target、标题和正文；需要附件时可在发布前上传，详见 [Managing releases](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)。

### 9. 公开访客验收

退出 GitHub 登录，或打开无痕窗口，逐项确认：

- [ ] `https://github.com/<GITHUB_USERNAME>/shushu-study-toolbox` 能打开；
- [ ] README 三张图片正常显示；
- [ ] About 描述和 12 个 Topics 正常显示；
- [ ] Actions 页面最新 CI 全绿；
- [ ] Releases 中能看到 `v1.0.0`；
- [ ] 点击 Code → Download ZIP 能下载；
- [ ] 搜索仓库名能找到公开仓库。

全部通过后，才能发布小红书正文和首评链接。

---

## 路线 B：网页创建空仓库 + Git 推送

### 1. 在网页创建空仓库

1. 登录 GitHub，右上角 `+` → **New repository**。
2. Owner 选择你的个人账号。
3. Repository name 填 `shushu-study-toolbox`。
4. Description 从内容文档复制。
5. Visibility 选择 **Public**。
6. **不要勾选** Add a README、Add `.gitignore` 或 Choose a license。
7. 点击 **Create repository**。

已有本地仓库导入时，GitHub 官方明确建议不要在网页端预填 README、`.gitignore` 或 License，以免制造合并冲突；见 [Creating a new repository](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository)。

### 2. 连接并推送

把 `<GITHUB_USERNAME>` 换成你的真实用户名：

```bash
cd ~/Desktop/shushu-study-toolbox
git remote add origin https://github.com/<GITHUB_USERNAME>/shushu-study-toolbox.git
git remote -v
git push -u origin main
```

推送完成后，回到路线 A 的第 6 步继续。

---

## 可选：设置社交预览图

这不是首次发布的阻塞项。GitHub 推荐使用小于 1 MB 的 PNG/JPG/GIF，至少 640×320，最佳 1280×640；小红书 3:4 竖图不能直接上传当社交预览。

路径：仓库 **Settings** → **Social preview** → **Edit** → **Upload an image**。官方规格见 [Customizing your repository's social media preview](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/customizing-your-repositorys-social-media-preview)。

## 常见问题

### `gh auth status` 显示错账号

先运行 `gh auth logout`，再执行 `gh auth login`，不要把仓库发布到错误账号。

### `remote origin already exists`

先检查：

```bash
git remote -v
```

若 URL 正确，直接 `git push -u origin main`；若不正确，先停下确认，不要盲目覆盖。

### 推送后 README 图片不显示

检查图片是否已经被 Git 跟踪：

```bash
git ls-files docs/assets
```

再确认 README 使用相对路径和大小写完全一致。

### 以后怎么更新

```bash
git status --short
git add <这次真正修改的文件>
git diff --cached
git commit -m "docs: 写清这次改了什么"
git push
```

每次推送后都重新确认 CI，不要跳过。
