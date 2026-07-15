# GitHub 发布指南（Jim 手动执行）

这份指南只准备命令，不会替你创建远端仓库或推送。请由 Jim 在确认当前分支内容、公开范围和合规声明后，亲手完成发布。

## 发布前

- 安装 [GitHub CLI](https://cli.github.com/)（命令 `gh`）。
- 确认终端所在的本地仓库就是准备公开的版本，且没有误放的隐私文件。
- 以下第 2 步适用于 GitHub 上还没有同名仓库的首次发布。

## 1. 确认登录

```bash
gh auth status
```

**这步在干什么：** 检查 GitHub CLI 当前登录的是哪个 GitHub 账号，以及凭据是否可用于仓库操作。

**成功长什么样：** 输出当前账号，并显示已登录到 `github.com`；若提示未登录，先运行 `gh auth login`，再回来重试。

## 2. 在 GitHub 建公开仓库并推送

```bash
cd ~/Desktop/shushu-study-toolbox
gh repo create shushu-study-toolbox --public --source=. --push
```

**这步在干什么：** 以当前本地 Git 仓库为来源，在你的 GitHub 账号下创建公开的 `shushu-study-toolbox` 仓库，设置远端并推送当前提交。

**成功长什么样：** 终端打印新仓库的 GitHub URL，推送完成后打开该 URL 能看到 README、源码和当前分支内容。

## 3. 补仓库描述与标签

```bash
gh repo edit --description "树树工具箱:把英文视频/资料变成双语学习笔记的 agent skill(下载/字幕/翻译/嵌入/笔记/总结)" \
  --add-topic claude-code --add-topic skill --add-topic bilingual --add-topic yt-dlp --add-topic whisper
```

**这步在干什么：** 给当前 GitHub 仓库设置一句话描述，并添加方便检索的 topics。

**成功长什么样：** 命令正常结束；刷新仓库首页后，右侧 About 区域出现上述描述以及五个 topics。

## 4. 看 CI 是否三平台全绿

```bash
gh run watch
```

**这步在干什么：** 选择并持续查看刚才推送触发的 GitHub Actions；CI 会覆盖 Ubuntu、macOS、Windows，以及 Python 3.11、3.13 的组合。

**成功长什么样：** 等待结束后，六个矩阵任务都显示完成且为绿色 `success`。若某项失败，可先用 `gh run view --log-failed` 查看失败日志，修复后按下节重新推送。

## 以后怎么更新

先在本地完成修改和测试，再执行 add / commit / push 三连；把提交说明替换成这次修改的真实内容：

```bash
cd ~/Desktop/shushu-study-toolbox
git add -A
git commit -m "docs: 写清这次改了什么"
git push
```

成功时，`git commit` 会打印新的提交摘要，`git push` 会显示远端分支已更新；随后再次运行 `gh run watch`，确认新一轮 CI 全绿。

## 命令参考

- [`gh repo create`](https://cli.github.com/manual/gh_repo_create)
- [`gh repo edit`](https://cli.github.com/manual/gh_repo_edit)
- [`gh run watch`](https://cli.github.com/manual/gh_run_watch)
- [Windows `mklink`](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/mklink)
