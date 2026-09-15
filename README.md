# CivitaiFetcher · 非猫 Civitai 信息补全器

一个 Windows 桌面小工具,为 Stable Diffusion 玩家把 **Civitai 模型管理**整条链路搬进本地:
模型信息补档、站内浏览下载、本地库分类整理,一站式完成。

产物与 SD WebUI 的 [Civitai Helper](https://github.com/zixaphir/Stable-Diffusion-Webui-Civitai-Helper)
元数据格式**双向兼容**——本工具写的缓存,插件直接认;插件扫的库,本工具直接用。

## 功能

### 扫描补档
遍历本地模型目录,按文件 SHA256 反查 Civitai,为每个模型生成
`.civitai.info` / `.json` / `.preview.png` 三件套。不在站上的(自练/已下架)写骨架标记,
支持重试。元数据采用**读-改-写合并**:用户手填的触发词、笔记与其他工具写入的
自定义字段永远不会被覆盖。

### Civitai 浏览
官网式布局:类型 / 基础模型 / Lora 次级分类(角色·服装·概念·画风…) /
排序 / 时段(含自定义日期区间)筛选,卡片无限滚动,已下载模型弱化标记,
详情页版本方块切换,悬浮一键下载。支持官方站与镜像站数据源,可登录 API Key。

### 下载队列
单线程顺序下载 + Range 断点续传,可暂停 / 继续 / 取消 / 失败重试;
**下载即建档**——模型落盘的同时自动写入三件套,并按 Civitai 大类
(角色 / 画风 / 服装 …)自动归类到子文件夹。

### 整理归类
把本地已有的模型库按 Civitai 标签大类重新归位:预览清单 → 确认移动 → 一键撤销;
模型连同全部伴生文件(info / json / 预览图)整组搬移,中英文文件夹映射可自定义。

## 下载

到 [Releases](../../releases) 页:

| 文件 | 说明 |
|---|---|
| `CivitaiFetcher.exe` | 主程序,联网使用 |

单文件 exe,免安装;设置 / 缓存 / 下载历史都存在 exe 同目录,不写注册表。

## 从源码运行与构建

```bash
# 源码直接跑(需 Python 3.10+,requests,pywebview)
python fetcher_app.py

# 前端(COSS 组件风格,React 19 + Vite + Tailwind v4):
#   将 frontend/ 下的 fetcher.html、vite.config.fetcher.ts 拷到你的 Vite 项目根,
#   frontend/src/fetcher 拷到 src/,执行 `vite build --config vite.config.fetcher.ts`,
#   产物输出到 ../civitai-fetcher/ui-dist(见 vite 配置),并拷贝 MiSans 字体到 ui-dist/fonts。

# 打包单 exe(PyInstaller)
pyinstaller --onefile --windowed --name CivitaiFetcher \
  --icon app.ico --add-data "ui-dist;ui-dist" --collect-all webview fetcher_app.py
```

命令行形态亦可用:`python civitai_fetcher.py --dry-run`(统计待补信息)等。

## 技术笔记

- 界面:pywebview + WebView2 承载 React 前端,js_api 桥纯方法 + evaluate_js 事件流;
- 网络层:显式直连策略(避开系统代理陷阱),请求限速与退避重试,缩略图两级本地缓存;
- 元数据:合并写入(读-改-写),自有键管理与外来键保留,与插件生态共存;
- 下载:`?token=` 鉴权过 CDN 302,`.downloading` 临时文件断点续传。

界面字体 MiSans(小米,免费商用许可);灵感与元数据格式来自 Civitai Helper 插件。

## License

MIT(见 LICENSE)。
