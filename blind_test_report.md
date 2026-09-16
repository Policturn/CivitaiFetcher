# CivitaiFetcher 盲测体检报告(black-box)

- 测试日期:2026-09-17
- 测试对象:`civitai-fetcher/`(fetcher_app.py + civitai_fetcher/browser/downloader/reorganize/compat,前端 ui-dist `fetcher-CTz_mSGN.js`)
- 测试方式:源码级直调(import + SimpleNamespace 参数)+ 真实 Civitai API + 本地慢速 Range 服务器;真实下载用 TextualInversion 最小文件(easyNegative,模型 7808 / 版本 9208,24KB);全部写入沙箱 `test_blind_*/`(已清理)
- 环境网络全程可用,无 SKIP 项
- 探针脚本留存:`开发/scripts/scratch/blind_*.py`(blind_common 为公共脚手架),运行日志 `blind_*.log`

**总成绩:44 项 PASS / 6 项 FAIL / 0 SKIP / 2 项 INFO 观察。发现 1 个严重死锁、1 个统计累积缺陷、4 个低危问题。**

---

## 一、缺陷汇总(按严重度排序)

### 缺陷 1(严重,可冻结整个下载队列):取消/暂停「排队中」任务死锁
- **位置**:`civitai_downloader.py` — `cancel_download`(L146-159)与 `pause_download`(L162-174)在持有非重入锁 `_DL_STATE["lock"]`(L26,`threading.Lock`)时调用 `_finish`(L65-73)/`_emit_tasks`(L104-111),二者内部会**再次**抢同一把锁 → 自死锁。`resume_download`(L177-189)为同型代码(锁内调 `_emit_tasks`),因暂停排队任务本身已死锁,实际入口暂不可达。
- **影响**:用户在下载队列里对一个尚未开始的任务点「取消」或「暂停」→ 调用线程永久挂起(pywebview js_api 调用不返回);死锁线程永久持锁,worker 完成当前任务时的 `_finish` 也随之挂死 → **整个下载队列冻结,只能重启应用**。
- **复现**(blind_h.py,已实测两次复现):
  1. 入队下载 A(easyNegative),等待其进入 active;
  2. 入队下载 B(B 处于 queued);
  3. 对 B 调 `cancel_download(B.id)`。
- **实际输出**:
  ```
  [FAIL(死锁)] H:cancel_queued: 取消『排队中』任务: 挂起=True 返回=None 耗时=0.00s
  [FAIL(死锁)] H:pause_queued:  暂停『排队中』任务: 挂起=True 返回=None 耗时=0.00s
  ```
  对照组:取消「下载中」的任务立即返回 `[PASS] H:cancel_active: 返回=True 耗时=0.00s`,证明死锁仅在排队分支。

### 缺陷 2(中,功能/数据展示):扫描统计跨次累积,GUI 显示虚高
- **位置**:`civitai_fetcher.py` — 模块级 `stats`(L70-73)与 `report`(L69)在 `run_scan`(L703-765)中**从不重置**。
- **影响**:同一进程内(GUI 每次点扫描都是同一进程)第二次扫描的 `done` 事件 stats 是历史累计值;`last_scan_report.json` 的 items 也跨次累积。前端按「本次扫描」展示即虚高。
- **证据**(blind_a.log / blind_a2.log):A2 扫 2 个文件后 `scanned=2`;紧接着 retry_skeletons 复扫后同一计数一路累加为 4、8;改用增量对比才能得出正确的单次结论(A3 增量复测:off 次 scanned=0/skipped=2,on 次 scanned=2,行为本身正确——**缺陷仅在统计不重置**)。

### 缺陷 3(低-中,体验/健壮性):非法 types 触发 400 后无意义重试 ~19s 再抛异常
- **位置**:`civitai_browser.py` `_get`(L121-155):仅 401/403/404 免重试,其余(含确定性的 400)按退避曲线重试 5 次。
- **复现**:`cb.search_models({"types": ["NotAType123"], "limit": 5})`
- **实际输出**:`[FAIL] B1f 非法 types: 抛异常: RuntimeError('http_400')`(耗时约 19s,6 次请求)。前端表现为浏览卡 ~19s 后报错,而非快速空结果。

### 缺陷 4(低,数据/计数):重整理预览把 `.vae.pt` 伴生文件当独立模型计数
- **位置**:`civitai_reorganize.py` — `MODEL_EXTS` 含 `.pt`(L22)而 `.vae.pt` 又在伴生清单 `COMPANION_SUFFIXES`(L18-21),`preview_reorganize` 的 os.walk 把 `xxx.vae.pt` 也当模型读 `xxx.vae.civitai.info` → 计入 noInfo。
- **证据**(blind_d.log,blind_d.py 用例 1 自带 `m_char.vae.pt`):`counts={'moves': 5, 'alreadyOk': 1, 'noInfo': 2, 'noHit': 1}`,期望 noInfo=1,多出的正是 `m_char.vae.pt`。移动本身不受影响(D2 实测 `.vae.pt` 正确随主模型搬移),仅清单/计数误导。

### 缺陷 5(低,数据/归类缺口):分类映射缺 "animals" 复数形态
- **位置**:`civitai_browser.py` `DEFAULT_CATEGORY_MAP`(L49-56):`"animal": "动物"` 存在,但 `"animals"` 缺失(而 pose/poses、object/objects、building/buildings、asset/assets 均已成对补齐)。
- **证据**:blind_d.py 实测 `resolve_category(["animals"]) → (None, None)`;且 civitai.com 上 `tag=animals` 为活跃标签(API 实查有 Nova Furry XL 等模型)。带该标签的模型不会被归入「动物」,落入 noHit 留在原地。

### 缺陷 6(低,健壮性):`run_scan` 的 types 传数组直接崩
- **位置**:`civitai_fetcher.py` L712 `args.types.split(",")`。
- **证据**:`cf.run_scan(types=["lora"])` → `AttributeError("'list' object has no attribute 'split'")`。前端固定传逗号串(已审查 bundle:`Object.keys(m).filter(...).join(",")`),仅 API 直调/第三方集成可触发。

### 观察(INFO,不计缺陷)
- **O1**:`retry_download` 对跨会话历史任务静默返回 False(`civitai_downloader.py` L192-208;落盘历史剥离 `_opts`,L44-51)。重启后历史里的「重试」按钮无效果也无提示。
- **O2**:设置里 `pinned_folders` 为**全量替换**语义(漏传的绑定即被删除,`fetcher_app.py` L373-376)。前端固定传全量(bundle 已核实),UI 无恙;但 API 直调传部分字典会误删其他绑定。
- **O3**:下载事件存在 `fetching` 先于 `enqueued` 到达的顺序翻转(enqueue 尚未返回 worker 已领任务)。因事件载荷为全量快照,UI 无实际影响。

### 测试过程自伤声明
- `civitai_fetcher.run_scan` 把扫描报告硬编码写到脚本目录(`civitai_fetcher.py` L755 用 `os.path.dirname(os.path.abspath(__file__))`,不受沙箱重定向控制),多轮真实扫描**覆盖了项目根目录的 `last_scan_report.json`**(原 177KB,2026-09-16 19:29 真实库扫描记录)。备份链路因二次 setup 被测试期文件覆盖,该文件无法找回。此文件为每次扫描覆盖写的运行时产物(.gitignore 不入库),不影响功能,特此如实说明。其余真实文件(gui_settings.json / download_history.json / hash_cache.json / compat_rules.json / reorganize_undo.json / category_folders.json / thumb_cache)均已验证未被污染。

---

## 二、逐项测试结果

### A. 扫描补档
| 项 | 结果 | 证据 |
|---|---|---|
| A1a dry-run types=lora | PASS | `{'total': 2, 'need_info': 2, 'have_preview': 0, 'no_preview': 2}` |
| A1b types=lora,lycoris | PASS | total=3 |
| A1c types=空 | PASS | total=0,不崩 |
| A1d 含不存在类型 | PASS | total=2,未知类型静默忽略 |
| A1e types=list | **FAIL(缺陷 6)** | AttributeError 崩 |
| A2 真实扫描(骨架/info/json) | PASS | 2 文件均 `skeleton_file=true` 的 .info + .json 落盘,stats `skeleton=2 api_not_found=2` |
| A2b preview 下载 | PASS | 真实 CDN 图 → `downloaded`,.preview.png 落盘,无 .downloading 残留 |
| A3 retry_skeletons on/off | PASS | 增量:off `scanned=0 skipped=2`;on `scanned=2 skeleton=2` |
| A3b 统计累积 | **缺陷 2 确认** | stats/report 模块级累计,done 事件虚高 |
| A4 force_preview on/off | PASS | 增量:off `preview_exists=1`;on `preview_exists=0, preview_fail=2`(已有图被强刷) |
| A5 stop_scan 中途停止 | PASS | stop 后 worker 退出、done 事件到达、无 crash、可立即重启扫描(28 事件) |

### B. 浏览
| 项 | 结果 | 证据 |
|---|---|---|
| B1a LORA×10 | PASS | 10 items,类型纯净 |
| B1b LORA+Checkpoint | PASS | types_seen={'LORA','Checkpoint'} |
| B1c 空 types | PASS | 5 items(通用搜索) |
| B1d baseModels=SDXL 1.0 | PASS | baseModel 全为 SDXL 1.0 |
| B1e cursor 翻页连续性 | PASS | page1=20 page2=20 **重叠=0** |
| B1f 非法 types | **FAIL(缺陷 3)** | RuntimeError('http_400'),19s 重试后抛 |
| B1g 超长 query(500 字符) | PASS | 200,0 items |
| B1h 日期区间聚合(_range_search) | PASS | 5 items 全在区间内,翻页重叠=0 |
| B2a save_browse→get_initial | PASS | 12 字段读回零差异 |
| B2b 增量合并不丢字段 | PASS | 只改 typeTab,tag 保留 |
| B2c 非 dict 防线 | PASS | 传 list 被忽略,原值保留 |
| B3a local_library_index | PASS | modelIds/versionIds 命中,骨架排除 |
| B3b 前端 isDownloaded 判定 | PASS | bundle 审查:`modelIds.has(String(id)) || versionIds.has(String(version.id))`,done 下载事件增量补集,逻辑自洽 |

### C. 下载(沙箱,真实 API + 本地 Range 服务器)
| 项 | 结果 | 证据 |
|---|---|---|
| C1 正常下载+状态流转+四件套 | PASS | `fetching→downloading→done` 事件齐全;easynegative.safetensors + .civitai.info + .json + preview 四件齐落 `dl1/embeddings/` |
| C2 重复入队 | PASS | `status=done, error='文件已存在,跳过下载(已补全信息)'`,非 failed |
| C3 下载中 cancel | PASS | 2.6MB/3.1MB 处取消,`.downloading` 保留,状态 cancelled(本地慢速 Range 服务器驱动 `_download_one`) |
| C4 autoCategory true/false | PASS | true→`dl2/embeddings/工具`(category='工具 (tool)');false→`dl1/embeddings/` 直下 |
| C5 modelName/versionName 传递 | PASS | 排队态显示传入名('前端传入名C5');完成后为 API 名('EasyNegative') |
| C6 Range 断点续传 | PASS | 重入队后 206 续传,0.4s 完成(全量需≥2.3s),SHA256 与源一致,残留清理 |

### D. 整理归类
| 项 | 结果 | 证据 |
|---|---|---|
| D1 preview 混合目录 | **FAIL(缺陷 4,低)** | moves=5/noHit=1/alreadyOk=1 正确;noInfo=2 应为 1(.vae.pt 被当模型) |
| D2 apply 整组移动 | PASS | executed=5 skipped=0;伴生 .info/.json/.preview.png/.txt/.vae.pt 全部随迁,源目录清空 |
| D3 undo 完整恢复 | PASS | restored=14 errors=[],文件归位,搬空的大类目录已清 |
| D4 让位/单复数/force | **FAIL(缺陷 5,低)** | 9 例中 8 例通过(concept 独占→概念、concept+style→画风让位、pose/poses→姿势、force 优先、自定义映射覆盖);唯 "animals"→None |

### E. 兼容性筛选
| 项 | 结果 | 证据 |
|---|---|---|
| E1a incompat | PASS | moves=[pony, realmeta],实测图 meta 优先于标注 baseModel;骨架/无info/other 正确排除 |
| E1b/E1c perfect/good | PASS | WAI-illustrious→perfect;Illustrious-XL 对 wai 基准→good |
| E1d 组合勾选 | PASS | moves=3,by_level={'incompat':2,'perfect':1} |
| E1e baseKey 校验 | PASS | `{'error': '未知基准基模: no_such_base'}` |
| E1f 非法等级过滤 | PASS | 未知等级被剔除,正常出计划 |
| E2 目录不存在 | PASS | 待测夹/扫描夹分别返回明确中文错误 |
| E3 规则损坏降级 | PASS | 损坏 JSON→内置、空表→内置、缺失→自动重建;judge 冒烟 pony→incompat / sdxl→weak / unknown→unknown / 双命中→mixed |

### F. 设置持久化(设置文件重定向到沙箱,真实验证未污染)
| 项 | 结果 | 证据 |
|---|---|---|
| F1 save_settings→get_initial | PASS | proxy/api_key/api_source/defaults/pinned 全一致,types/browse/options 保留 |
| F1b 非法 api_source 防线 | PASS | 'evil.example' 被拒,保持 red |
| F2a 空串解绑 | PASS | 「角色」解绑,「画风」保留 |
| F2b 漏传键语义 | INFO(观察 O2) | 全量替换:只传 {画风} 后「角色」被删;前端契约匹配 |
| F2c 非 dict 防线 | PASS | 传 list 被忽略 |
| F3 defaults 增量合并 | PASS | 旧 dlRoot + 新 dlAutoCategory 并存 |
| F3b 无设置文件兜底 | PASS | 默认 webui_root/空 browse/demo=false |
| F4 扫描参数不污染设置(回归) | PASS | start_scan(types=逗号串)后 settings.types 仍为 dict 勾选状态,webui_root 按设计更新——历史事故防线有效 |

### G. 边界与异常
| 项 | 结果 | 证据 |
|---|---|---|
| G1a-d webui_root 不存在 | PASS | dry-run total=0;真实扫描空跑不崩;索引返回空;reorganize 返回「目录不存在」 |
| G2 空目录扫描/重整理 | PASS | 全零统计 + 空计划,无异常 |
| G3 扫描中再扫描/再下载 | PASS | 二次 start_scan 返回 False(互斥);并行真实下载 done、无 crash 事件、扫描正常完结 |
| G4 空 Key 浏览 NSFW | PASS | 无 Key 直连返回 20 items,其中 17 个 nsfw=true;check_api_key('') 优雅报「Key 无效或已过期」 |

### H/I. 加测(清单外:排队任务死锁、从未测过的路径)
| 项 | 结果 | 证据 |
|---|---|---|
| H cancel_queued | **FAIL(缺陷 1,严重)** | 调用线程挂起 >6s 不返回,锁永久被占 |
| H pause_queued | **FAIL(缺陷 1,严重)** | 同上 |
| H cancel_active(对照) | PASS | 立即返回 True |
| I1 历史任务 retry(跨会话) | PASS(观察 O1) | 静默返回 False,无法重试旧历史 |
| I2 search_images 图墙 | PASS | 5 items + nextCursor |
| I3 get_model_detail | PASS | name/versions/files 齐全 |
| I4 read_image_params | PASS | 无参数图优雅返回 ok=False「PNG 内无生成信息」 |

---

## 三、结论

- **必须修**:缺陷 1(排队任务 cancel/pause 死锁,一处模式波及 cancel/pause/resume 三条路径,修复方向是统一在锁外调 `_finish`/`_emit_tasks` 或换 `RLock`——本报告仅记录,未做任何修改)。
- **建议修**:缺陷 2(每次 `run_scan` 开头重置 stats/report)、缺陷 3(400/4xx 客户端错误免重试)、缺陷 5(补 "animals" 映射)、缺陷 4(重整理跳过 `.vae.pt` 计数)。
- 扫描、浏览翻页、下载主链路(含断点续传/重复入队/自动归类/建档四件套)、整理与撤销、兼容性引擎、设置持久化(含历史事故回归防线)整体质量良好,44/50 项通过。
