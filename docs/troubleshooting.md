# 排错

## 症状对照表

| 现象 | 原因与解决 |
|---|---|
| 手机完全收不到 | 先跑 `selftest.py`。测试能收到而任务不触发，说明 hooks 没生效，先确认是否完全重启了宿主 |
| 只有测试消息收得到，真实任务一条都没有 | 编码崩溃，见下面的编码事故复盘。先跑 `selftest.py --encoding` |
| 日志里有记录、手机没有 | 手机通道没启用或凭证没填全，通道列表状态应该是「已启用」 |
| 微信返回 40013 | appID 填错，回测试号页面重新复制 |
| 微信返回 40125 | appsecret 填错，同上 |
| 微信返回 43004 | 没有关注这个测试号。用同一个微信扫码关注后再发 |
| 微信返回 45009 | 接口调用超频，等 10 分钟。本程序已缓存 token，正常不会触发 |
| 微信收到消息但正文空白，或只剩一个词如 `test` | 两个原因，跑 `selftest.py --wxmp` 会逐行指出：模板变量前缺「标签：」导致那几行渲染为空；剩下的就只有模板标题。见 [channels/wechat-sandbox.md](channels/wechat-sandbox.md) |
| 接口返回 `errcode: 0` 但手机没收到 | 关注测试号的微信和 openid 是不是同一个；微信里是不是把这个号设成了「不提醒」；跑 `--wxmp` 确认模板写法 |
| 微信消息点不动 | 模板消息不传 `url` 就没有跳转，微信的设计如此。正文能正常显示就不需要点，想要跳转就在通道配置的 `url` 里填个网址 |
| 通知来得很慢或者没有 | 检查「最短耗时」门槛，再看 `logs/notify-日期.log` 里的 `skip ... reason=...` |
| 推送正文是乱码 `閫氱煡` | 同一类编码问题，见下面的复盘。已修复，若再现说明有人把 `read_stdin` 改回了 `sys.stdin.read()` |
| 想临时静音 | 设置中心取消「总开关」，或托盘菜单取消「通知已开启」 |
| 改了模板没变化 | 模板改动要点「保存配置」 |
| 任务完成后通知两次 | 检查是否有别的工具也注册了 `Stop` hook。本程序已按内容指纹去重 |
| 界面里 ✅ 显示成方框勾 | Tk 8.6 在 Windows 上不支持彩色 emoji。只影响界面，推到手机的是彩色 emoji |

## 日志在哪

`logs/notify-YYYY-MM-DD.log` 是运行日志，记了哪个事件、是否放行、走了哪个通道。
`logs/notifications.log` 是已发送通知的完整内容留档。

日志默认保留 14 天，改 `config.json` 里的 `advanced.log_retention_days` 可以调整。
两个文件落盘前都过了一遍脱敏，凭证不会进日志。

## 编码事故复盘：中文变成落单代理字符

### 症状

`selftest.py` 的测试消息收得到，但 WorkBuddy 真实跑完任务后手机毫无动静。
日志里是一串这样的东西：

```
UnicodeEncodeError: 'utf-8' codec can't encode character '\udca2'
: surrogates not allowed
```

### 成因

两层问题叠在一起。

第一层，宿主投递给 hook 的 stdin 是 UTF-8 字节，但 Windows 上 Python 的 `sys.stdin`
默认按系统 ANSI 代码页（中文是 cp936）加 surrogateescape 解码。于是 `通知`
被解成 `閫氱煡`，无法配对的多余字节变成 `\udca2` 这种落单代理字符。

第二层，代理字符不是合法 Unicode。紧接着 `json.dumps(...).encode("utf-8")`
直接抛 `UnicodeEncodeError`，整条通知在投递给派发进程之前就消失了。

而 `selftest.py` 不经过 stdin，所以它一直正常。这就是「只有测试消息能收到」的原因。

### 修法

`hook.py` 和 `dispatch.py` 三处改动：

- 读 `sys.stdin.buffer` 的原始字节，自己按 UTF-8 显式解码，把解码权从宿主环境手里拿回来
- 入口处 `sanitize.clean_obj()` 递归清洗。落单代理字符按 surrogateescape 反推回原始字节
  再重解，中文可以完整复原；实在救不回来的用 `U+FFFD` 占位
- 投递、去重哈希、通道打包、状态落盘等所有 `encode("utf-8")` 都补上 `errors="replace"`
  兜底。宁可坏一个字符，也不能丢一整条通知

### 回归防线

```bash
.venv\Scripts\python.exe selftest.py --encoding
```

会检查四项：脏字符串能清洗、正常中文和 emoji 幂等不变、受损字节能还原、渲染打包不抛异常。
`tests/test_sanitize.py` 里也有对应的 pytest 用例。

别把 `read_stdin` 改回 `sys.stdin.read()`，那会把这个 bug 原样带回来。

## 已知限制

1. 微信测试号是「开发测试」定位，微信官方不承诺永久不变。若某天接口调整，
   先用 `selftest.py --status` 确认，再换企业微信自建应用（同样免费免实名）。
   它是目前唯一能做到不实名、不付费、消息进微信的路子。
2. Tkinter 里 emoji 是黑白的。Tk 8.6 在 Windows 上不支持彩色 emoji，
   ✅ 会显示成一个方框勾。只影响界面，推到手机的是正常彩色 emoji。
3. `config.json` 里凭证是明文（appsecret、SMTP 授权码、webhook key）。
   程序每次保存后会把该文件的 ACL 收紧成仅当前用户可读写，
   `state/token_cache.json` 同理，但不要把这两个文件传到公开仓库。
4. WorkBuddy 的 `PermissionRequest` 在「不询问」权限模式下不会触发。
   想测权限类通知，需把会话权限模式切回「询问」。
5. hooks 事件名以宿主版本为准。升级后不触发了，先用 `selftest.py --status`
   确认挂载状态，再对照宿主最新文档核对事件名。
6. 任务栏图标是品牌青绿色铃铛，由 `make_icon.py` 生成 9 档尺寸。程序启动时会设置
   独立的 AppUserModelID，否则 Windows 会把窗口归到 Python 名下，任务栏显示的就是
   Python 图标。想换图标改完跑一次 `python make_icon.py`。
7. 微信测试号的昵称和头像是固定的，改不了。它没有独立后台，昵称
   `wxd_xxxx的接口测试号` 由 appid 派生，头像也不可设置。想要好看的名称和头像只能
   注册订阅号（个人可注册，但用不了模板消息）、注册服务号并做微信认证
   （需企业资质，认证费 300 元/年），或者改用企业微信自建应用
   （可自定义应用名称和 logo，免费免实名）。个人自用的话测试号仍是性价比最高的选择。
8. 详情最多四行，每行约 18 个汉字。微信卡片一个变量只占一行，而 `\n` 在新版卡片里
   不但不换行，还会把换行之后的内容整段丢掉，所以多行只能靠多声明变量实现
   （`detail`、`detail2`、`detail3`、`detail4`）。程序会按显示宽度切段、行首避标点、
   英文单词不劈开，超出四行才加 `…`。想彻底不受行数限制，改用企业微信自建应用。
9. `Notification` 里的空闲提醒不会通知。宿主答完在等你下一条消息时也会发
   `Notification`（内容是 `xxx is waiting for your input`），但那和 `Stop` 报的
   「任务完成」是同一件事，再推一条就是重复打扰，所以已在 `events.py` 里过滤。
   真正的权限请求（`permission_prompt`）和确认对话框仍会正常通知。
