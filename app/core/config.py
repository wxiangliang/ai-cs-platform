"""全局配置模块。

使用 pydantic-settings 统一管理配置，支持从环境变量 / .env 文件读取，
禁止在业务代码中写死数据库地址、Redis 地址、API Key 等敏感信息。
"""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 视为「生产形态」的环境：不满足安全底线拒绝启动（Stage 13 硬门禁）
_PROD_ENVS = ("staging", "prod", "production")


class Settings(BaseSettings):
    """应用全局配置。

    所有字段都可以通过 .env 文件或环境变量覆盖（大小写不敏感）。
    生产环境通过环境变量注入，本地开发使用项目根目录的 .env 文件。
    """

    # ----- 应用基础信息 -----
    APP_NAME: str = "ai-cs-platform"
    # 运行环境：local / dev / staging / prod，用于区分不同环境行为
    APP_ENV: str = "local"
    # 是否开启调试模式（生产环境必须为 False）
    DEBUG: bool = False

    # ----- 数据库配置 -----
    # 必须使用 postgresql+asyncpg:// 前缀，保证走 async 驱动
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_cs_platform"
    # 连接池大小与溢出配置，避免每次请求新建连接
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    # 连接池获取连接的超时时间（秒），防止数据库故障时请求无限阻塞
    DB_POOL_TIMEOUT: int = 30
    # 连接回收时间（秒），避免使用被数据库单方面关闭的长连接
    DB_POOL_RECYCLE: int = 1800
    # 建立连接的超时时间（秒）
    DB_CONNECT_TIMEOUT: int = 10
    # 单条 SQL 语句执行超时（秒）：慢查询/锁等待不允许无限挂起请求
    DB_COMMAND_TIMEOUT: int = 30
    # 是否打印 SQL（仅调试用，生产关闭）
    DB_ECHO: bool = False

    # ----- Redis 配置 -----
    REDIS_URL: str = "redis://localhost:6379/0"
    # Redis 操作超时时间（秒），所有外部访问必须有超时
    REDIS_SOCKET_TIMEOUT: int = 5
    REDIS_CONNECT_TIMEOUT: int = 5

    # ----- 日志配置 -----
    LOG_LEVEL: str = "INFO"

    # ----- LLM 配置 -----
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    CHAT_MODEL: str = "gpt-4o-mini"
    # LLM 调用超时（秒），所有外部访问必须有超时
    LLM_TIMEOUT: int = 30
    # 轮级 LLM 时间预算（秒，0=关闭）：一轮对话内所有 LLM 调用共享的时间上限。
    # 剩余预算耗尽后，后续 LLM 调用直接走降级（与无 Key 同路径：模板/摘录/规则），
    # 单次调用也以剩余预算为外层超时——防止多次串行调用 × 超时重试把单轮拖到分钟级
    # （正常轮次 2-3 次调用远用不满，仅剪掉病态尾部）
    TURN_LLM_BUDGET_SECONDS: float = 40.0
    # LLM 回复润色：把模板/事实底稿改写为自然话术（仅 DONE/FALLBACK 轮次，
    # 补槽/确认门保持确定性模板）。未配置 API Key 时自动失效
    LLM_REPLY_ENABLED: bool = True
    # SetFit 低置信难例的 LLM 二判（含意图边界裁决规则）。未配置 Key 时自动失效
    LLM_INTENT_SECOND_OPINION: bool = True
    # LLM 槽位抽取兜底：规则抽不全必填槽位时补抽（规则结果优先）。未配置 Key 时自动失效
    LLM_SLOT_FALLBACK: bool = True
    # 注入 LLM 的最近对话轮数上限
    LLM_HISTORY_MAX_TURNS: int = 4

    # ----- Stage 17 LLM 成本控制 -----
    # 模型分级路由：简单轮（二判/槽位/短润色 purpose=classify）走 fast 小模型，
    # RAG 生成/复杂润色（purpose=generate）走 smart 大模型；留空回落 CHAT_MODEL（零回归）
    CHAT_MODEL_FAST: str = ""
    CHAT_MODEL_SMART: str = ""
    # 租户 token 预算与熔断：达日预算后 LLM 增强层全部降级模板（与无 Key 同路径）。
    # 默认关闭；DAILY_TOKENS=0 表示不限
    LLM_BUDGET_ENABLED: bool = False
    LLM_BUDGET_DAILY_TOKENS: int = 0
    # 语义缓存：可缓存轮次（FAQ/RAG/闲聊，无副作用）按查询 embedding 相似度直答。
    # 默认关闭（改变答案时效性，需显式开启）；严格租户隔离 + TTL
    SEMANTIC_CACHE_ENABLED: bool = False
    SEMANTIC_CACHE_THRESHOLD: float = 0.92
    SEMANTIC_CACHE_TTL: int = 3600
    SEMANTIC_CACHE_MAX_PER_TENANT: int = 200

    # ----- Stage 18 A/B 实验框架 -----
    # 实验配置文件路径（JSON）；留空=无实验运行（主链路零回归）。
    # 变体只能覆盖白名单内的已有配置项，不引新代码分支
    EXPERIMENTS_CONFIG_PATH: str = ""

    # ----- 意图分类器（Stage 04-02 SetFit）-----
    # rule：纯规则（Stage 03 行为）；hybrid：规则控制层 + SetFit 语义层（推荐）
    INTENT_CLASSIFIER: str = "rule"
    # SetFit 模型目录（scripts/train_setfit_intent.py 的产物）
    SETFIT_MODEL_PATH: str = "models/intent_setfit_v1"
    # 语义分类置信度阈值（读操作意图）：低于该值兜底 META.UNKNOWN（走澄清/知识库）。
    # 按 val 集标定：0.40 → 覆盖率 97.7%、覆盖内精度 95.4%（见 stage-04-02 需求附录）
    INTENT_CONFIDENCE_THRESHOLD: float = 0.40
    # 写操作意图（退款/取消/改地址等 L2/L3）单独用更高阈值：错判代价高，宁可澄清
    INTENT_CONFIDENCE_THRESHOLD_WRITE: float = 0.60

    # ----- 鉴权与多租户加固（Stage 08）-----
    # 生产必须开启：tenant_id 从 API Key 凭证解析，请求体中的忽略；
    # false=开发模式（行为与 Stage 07 前完全一致，联调不被鉴权阻塞）
    AUTH_ENABLED: bool = False
    # 凭证验证结果的进程内缓存 TTL（秒）：bcrypt 验证有意做慢，避免每请求全量比对
    AUTH_CACHE_TTL: int = 300
    # 限流（Redis 滑动窗口；Redis 不可用时放行并告警，不做可用性单点）
    RATE_LIMIT_TENANT_PER_MINUTE: int = 600
    RATE_LIMIT_SESSION_PER_MINUTE: int = 30
    # 发消息幂等缓存 TTL（秒）与单条响应缓存大小上限（字节）
    IDEMPOTENCY_TTL: int = 600
    IDEMPOTENCY_MAX_BODY_BYTES: int = 65536
    # 请求体大小上限（字节）
    MAX_REQUEST_BODY_BYTES: int = 1048576
    # CORS 允许来源（逗号分隔，空=不启用 CORS）
    CORS_ORIGINS: str = ""

    # ----- 工具层与确认门（Stage 05 / Stage 11 MCP）-----
    # 工具提供方：mock（进程内确定性假数据）；mcp（MCP 服务提供订单/物流查询，
    # 服务未覆盖的工具与故障场景自动回落 mock）
    TOOL_PROVIDER: str = "mock"
    # 单次工具调用超时（秒）
    TOOL_TIMEOUT: int = 10
    # MCP 业务工具服务地址（streamable-http 端点，scripts/run_mcp_server.py）
    MCP_SERVER_URL: str = "http://localhost:8400/mcp"
    MCP_TIMEOUT: int = 10
    # MCP 调用失败的降级策略（Stage 13）：fail=如实报上游不可用（生产语义，
    # 绝不用 mock 编造订单事实）；mock=回落假数据+degraded 标记（仅开发联调）
    TOOL_MCP_FALLBACK: str = "fail"
    # Skill md 声明校验的告警是否升级为启动失败（CI 建议 true）
    SKILL_LOADER_STRICT: bool = False
    # 任务挂起栈深度上限（确认门/补槽中插入新任务时旧任务入栈）
    TASK_STACK_MAX: int = 2
    # 任务时效（分钟）：超过该时长未推进的任务加载时丢弃——
    # 用户隔很久回来，旧任务不该复活，新单号应服务新问题（Stage 10）
    TASK_TTL_MINUTES: int = 30
    # 同一任务追问上限：连续追问超过该次数放弃任务并建议转人工（Stage 10）
    TASK_MAX_ASKS: int = 3
    # 连续 N 轮 UNKNOWN 兜底 → 自动建转人工工单并询问（Stage 07）
    HANDOFF_UNKNOWN_STREAK: int = 2

    # ----- 内容安全护栏（Stage 14）-----
    # 总开关：规则层注入检测/违禁词/输出护栏（规则库损坏时自动降级放行）
    GUARDRAIL_ENABLED: bool = True
    # 重度违禁连击 N 次 → 建转人工工单（reason=ABUSE）并静默会话
    GUARDRAIL_ABUSE_STREAK: int = 2
    # 同会话同文本连发 N 次 → 拦截并提示换说法（防刷 LLM 成本）
    GUARDRAIL_REPEAT_LIMIT: int = 3

    # ----- 多语言地基（Stage 19）-----
    # 默认语言：面向用户文案与提示词的回退语言（zh 是源语言，逐字对齐现有中文）
    LOCALE_DEFAULT: str = "zh"
    # 支持的语言（逗号分隔）；请求 locale 不在此列表时回退默认
    SUPPORTED_LOCALES: str = "zh,en"

    # ----- 体验闭环（Stage 15）-----
    # 空闲会话自动关闭（小时）：超过该时长无消息的 active 会话由定时 CLI 置 closed
    SESSION_IDLE_CLOSE_HOURS: int = 24
    # 会话关闭时是否发送满意度询问
    CSAT_ON_CLOSE: bool = True
    # 已认领工单超时（小时）：ASSIGNED 超过该时长无坐席动作 → CLOSED + 会话归还
    TICKET_STALE_HOURS: int = 24

    # ----- Langfuse 链路追踪（Stage 12）-----
    # 双 Key 齐全且开关开启才启用；未配置/初始化失败静默降级，不影响主链路
    LANGFUSE_ENABLED: bool = True
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"

    # ----- 记忆系统（Stage 10）-----
    # local=自研（短期窗口+LLM 摘要+长期事实表）；mem0=接入 mem0（不可用自动降级 local）；off=关闭
    MEMORY_PROVIDER: str = "local"
    # 短期记忆：注入最近 N 条消息
    MEMORY_SHORT_TERM_TURNS: int = 8
    # 会话消息数超过该阈值后，把更早对话压缩成摘要（防上下文膨胀，需 LLM）
    MEMORY_SUMMARY_THRESHOLD: int = 20
    # 摘要滚动步长：距上次摘要新增消息数达到该值才再次触发 LLM 续写
    # （成本修复：原实现超阈值后每轮都调一次 LLM；每轮 2 条消息，6=约每 3 轮滚动一次。
    # 首次摘要不受步长限制；步长间隔内早于短期窗口的少量消息暂不在上下文中，下次滚动并入）
    MEMORY_SUMMARY_STEP: int = 6
    # 长期记忆：取用时最多注入 N 条持久事实
    MEMORY_LONG_TERM_MAX: int = 5
    # 后台记忆任务并发上限（容量修复）：每轮对话都会派生一个记忆写入任务，
    # 无上限时 N QPS 稳态下有 N 个任务与请求争抢同一 DB 连接池与 LLM 额度
    MEMORY_TASK_CONCURRENCY: int = 3

    # ----- Embedding 配置（Stage 06 RAG）-----
    # openai：走 OpenAI 兼容接口（OPENAI_BASE_URL 可指向本地推理服务）；
    # hash：本地确定性伪向量，仅供开发/测试离线跑通链路，禁止生产使用
    EMBEDDING_PROVIDER: str = "hash"
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    # 向量维度：Milvus collection 与 embedding 输出必须一致，变更需重建索引
    EMBEDDING_DIM: int = 512
    EMBEDDING_TIMEOUT: int = 15

    # ----- 知识库 / 向量检索配置（Stage 06）-----
    # 是否启用知识库检索（未部署 Milvus 时可关闭，主链路不受影响）
    KB_ENABLED: bool = True
    # 向量检索后端：当前实现 milvus；接口层预留 pgvector / elasticsearch
    KB_BACKEND: str = "milvus"
    MILVUS_URI: str = "http://localhost:19530"
    MILVUS_TOKEN: str = ""
    MILVUS_TIMEOUT: int = 5
    # FAQ 精确命中阈值（余弦相似度）：达到即直接返回标准答案。
    # 默认值按真实语义 embedding 校准；EMBEDDING_PROVIDER=hash（开发模式）时
    # 相似度整体偏低，建议 .env 里降到 0.6 / 0.25 左右
    FAQ_HIT_THRESHOLD: float = 0.88
    # 文档层拒答阈值：top1 低于该分数则拒答转人工，不编造
    RAG_MIN_SCORE: float = 0.60
    RAG_TOP_K: int = 5
    # ----- 检索管道 v2（Stage 06-04）-----
    # 多路召回宽度（每路），融合/重排后截到 RAG_TOP_K
    RAG_RECALL_TOP_K: int = 20
    # 重排器：off=关闭（RRF 序即终序）；local=本地 CrossEncoder（首次运行下载模型）
    RERANKER_PROVIDER: str = "off"
    RERANK_MODEL: str = "BAAI/bge-reranker-base"
    # 歧义检测：top1/top2 向量分差小于该值且来自不同文档 → 标记 ambiguous，
    # 摘录式回答附「以对应政策为准」提示（LLM 生成路径由资料并列呈现处理）
    RAG_AMBIGUITY_DELTA: float = 0.05
    # 含糊查询 LLM 改写（WeKnora 对齐）：指代/过短查询先结合近期对话改写成
    # 独立明确的检索查询再检索（「刚才那个多少钱」→「AP-300 空气净化器价格」）。
    # 无 Key 自动失效（零回归）；改写轮次绕过语义缓存（结果依赖对话上下文）
    RAG_QUERY_REWRITE_ENABLED: bool = True

    # ----- Stage 21 智能澄清 -----
    # UNKNOWN 轮次用 LLM 生成针对性澄清问句（结合 top_k 候选与近期对话），
    # 替代固定澄清模板；无 Key/失败降级模板（零回归）
    CLARIFY_LLM_ENABLED: bool = True
    # top_k 候选进入澄清 prompt 的最低置信分（宽松下限——低置信正是本场景）
    CLARIFY_MIN_CANDIDATE_SCORE: float = 0.15

    # ----- Stage 23 对话方向纠偏 -----
    # 中置信软确认：SETFIT/LLM 来源、置信低于该值的新开任务，追问话术前
    # 加意图复述（"您是想退货对吗？"）——不阻塞流程，用户扫一眼即可纠偏
    INTENT_SOFT_CONFIRM_THRESHOLD: float = 0.60

    # ----- Stage 26 意图决策加固（阈值均为待真实流量标定的保守默认）-----
    # SetFit top1-top2 最小分差：低于此值视为模糊难例（交 LLM 二判；
    # 二判不可用采纳 top1 但打 SETFIT_LOW_MARGIN、由软确认接住）
    INTENT_MIN_MARGIN: float = 0.10
    # 任务进行态切换阈值：补槽/确认门中新意图挂起切换所需的最低置信
    # （显式切换信号「另外/顺便」走普通阈值；证据不足保留任务二选一澄清）
    INTENT_SWITCH_THRESHOLD_COLLECTING: float = 0.78
    INTENT_SWITCH_THRESHOLD_CONFIRMING: float = 0.85
    # 写意图软确认线（单列高于采纳线 0.60，修复采纳线==软确认线的死区）
    INTENT_SOFT_CONFIRM_THRESHOLD_WRITE: float = 0.75

    # ----- 示例向量交叉验证（Stage 26 遗留 3，功能已实现**默认关闭**）-----
    # 启用时机与验收清单见 docs/intent/README.md「示例向量交叉验证与 LTR 路线」：
    # 建索引（scripts/build_intent_example_index.py）→ 意图评估门禁零回归 →
    # 上线观察二判量下降后保持开启。margin 小的难例先查训练集近邻，
    # 近邻同意 top1 → 免 LLM 二判直接采纳（SETFIT_KNN_CONFIRMED）
    INTENT_EXAMPLE_KNN_ENABLED: bool = False
    # 近邻数与确认所需最低平均余弦相似度（待真实 embedding 分布标定）
    INTENT_EXAMPLE_KNN_TOPK: int = 5
    INTENT_EXAMPLE_KNN_MIN_SIM: float = 0.65
    # 低置信闲聊救援线（家常开放域优化）：top1 是 CHITCHAT.* 且近邻同意、
    # 相似度过此线 → 免二判采纳。高于 margin 确认线——低置信场景要求更强证据
    INTENT_KNN_CHITCHAT_MIN_SIM: float = 0.70
    # 索引产物目录（不进 git，锚定仓库根）
    INTENT_EXAMPLE_INDEX_DIR: str = "models/intent_example_index"

    # ----- Stage 30 Conversation Mode Gate（对话模式门）-----
    # 规则控制层之后、业务意图分类之前判「闲聊/业务/混合/OOS」四模式，
    # v1 只接管高置信 SOCIAL_ONLY（免 LLM 二判直接闲聊回复，任务保持+续办提示），
    # 其余模式全部影子观察。默认关=零回归；产物缺失/异常一律 fail-open。
    # mode head 共享 SetFit body（一轮只编码一次），SetFit 重训后必须重训
    MODE_GATE_ENABLED: bool = False
    # 产物目录（scripts/train_mode_gate.py 生成，锚定仓库根）
    MODE_GATE_DIR: str = "models/mode_gate_v1"
    # SOCIAL_ONLY 直通线（校准后概率）与 top1-top2 分差线——冷启动基线，
    # 待真实流量按「业务误吞率/直通覆盖率/二判降幅」标定
    MODE_GATE_SOCIAL_MIN_SCORE: float = 0.88
    MODE_GATE_SOCIAL_MIN_MARGIN: float = 0.20
    # 任务进行中（COLLECTING/CONFIRMING）闲聊插话要求更高置信
    MODE_GATE_SOCIAL_MIN_SCORE_ACTIVE: float = 0.92
    # OOS 高置信直接回能力边界话术（跳过澄清 LLM）；合成 OOS 数据未经真实
    # 验证，默认关先影子观察误判分布
    MODE_GATE_OOS_REPLY_ENABLED: bool = False

    # ----- Stage 31 主动服务地基（NBA + 活动引导）-----
    # 主任务完成后是否追加至多一个主动动作（活动提示）。双开关分级：
    # ENABLED=计算+落决策日志（影子）；APPLY=真实追加进回复。默认双关=零回归。
    # 抑制矩阵（退款/投诉/确认门/负面情绪/闲聊轮禁营销）见 stage-31 需求第 4 节
    PROACTIVE_ENABLED: bool = False
    PROACTIVE_APPLY: bool = False
    # 活动池 JSON 配置（mtime 缓存自动重载，同 experiments 模式；缺失=无活动）
    CAMPAIGN_CONFIG_PATH: str = "configs/campaigns.json"
    # 会话内主动动作上限 / 拒绝后冷却小时数（频控全走 Redis，故障 fail-closed 抑制）
    PROACTIVE_SESSION_MAX: int = 1
    PROACTIVE_REJECT_COOLDOWN_HOURS: int = 48
    # Stage 33 会员注册建议（受总开关双关管控；未注册用户业务办完后建议开通，
    # 话术引导显式回复「注册」）；客户级建议上限
    PROACTIVE_ONBOARDING_ENABLED: bool = True
    PROACTIVE_ONBOARDING_MAX: int = 2

    # ----- Stage 41 会话主动引导与建议闭环 -----
    # 开场引导：会话创建时发一条轻量欢迎（受 PROACTIVE_ENABLED 总开关双关；
    # 同客户 24h 至多一次，opt-out/at_risk 不发，Redis 故障 fail-closed 不发）
    PROACTIVE_WELCOME_ENABLED: bool = False
    WELCOME_CONFIG_PATH: str = "configs/welcome.json"
    # 服务延伸候选池（P4：onboarding 之后、营销活动之前；空/缺失=无延伸）
    FOLLOWUP_CONFIG_PATH: str = "configs/followups.json"

    # ----- Stage 40 行为准则层（借鉴 Parlant，评估见 docs/architecture/parlant_evaluation.md）-----
    # 准则=「应该怎样」的引导（护栏管「绝不允许」）；只注入润色/RAG/澄清等
    # LLM 增强路径；v1 全规则匹配零 LLM 成本。默认关=零回归
    GUIDELINES_ENABLED: bool = False
    GUIDELINES_CONFIG_PATH: str = "configs/guidelines.json"
    # 注入条数封顶（防重蹈系统提示词膨胀——Parlant 教训的硬闸）
    GUIDELINES_MAX_INJECT: int = 3

    # ----- Stage 36 事件驱动主动客服 -----
    # 业务事件（POST /api/events）→ 幂等/退订/静默/重查最新事实 → 主动消息。
    # 默认关=零回归；静默时间如 "22-8"（跨零点），空=不启用
    EVENTS_ENABLED: bool = False
    EVENTS_QUIET_HOURS: str = ""

    # ----- Stage 35 身份核验分级（IAL）-----
    # 工具最低等级声明在 app/chat/tools/catalog.py（单一事实来源）；
    # 开发默认 2=沙盒互信零回归；生产按需下调（1 → 改地址等 IAL2 工具
    # 未达标结构性拒绝转人工核实）。OTP 渠道接入前 IAL2 不可达（遗留 1）
    IDENTITY_DEFAULT_LEVEL: int = 2
    IDENTITY_LEVEL_AUTHENTICATED: int = 1

    # ----- Stage 34 Case 工单与 SLA 治理 -----
    # SLA 解决时限（小时，按优先级）；超时由 cron（case_sla_check）升级 ESCALATED
    CASE_SLA_HOURS_HIGH: int = 4
    CASE_SLA_HOURS_NORMAL: int = 24
    CASE_SLA_HOURS_LOW: int = 72
    # 补偿政策表（Service Recovery，纯政策判定 LLM 不参与；示例
    # configs/compensation_policies.example.json，缺失=一律不符合资格）
    COMPENSATION_POLICY_PATH: str = "configs/compensation_policies.json"

    # ----- Stage 27 Meta-classifier 影子模式 -----
    # 影子模式：模型对每轮语义层决策做预测、落决策日志、出分歧率指标，
    # **不驱动任何决策**（合成数据模型不碰生产决策的红线）。
    # 模型产物缺失（models/ 不进镜像）时自动静默停用，零回归
    META_SHADOW_ENABLED: bool = True
    # 使用的模型：champion=按 metrics.json 冠军；或显式 lr/lgbm/xgb
    META_SHADOW_MODEL: str = "champion"
    # 产物目录（锚定仓库根，Stage 13 纪律同 SKILLS_DIR）
    META_SHADOW_DIR: str = "models/meta_classifier_v1"

    # ----- Stage 22 只读诊断 agent -----
    # 解释性查询（"为什么还没到"）的多步只读调查：LLM 在只读工具白名单内
    # 决定下一步查什么，代码控制终止。默认关闭=零回归；开启需真实 LLM 联调
    DIAGNOSE_AGENT_ENABLED: bool = False
    # 决策循环步数硬上限（结构性终止条件之一）
    DIAGNOSE_MAX_STEPS: int = 3
    # 父子分块：命中块按章节聚合上下文的字符上限
    RAG_SECTION_CONTEXT_CHARS: int = 1200
    # 分块参数（字符数口径）
    KB_CHUNK_SIZE: int = 500
    KB_CHUNK_OVERLAP: int = 50
    # 碎片块合并阈值：短于该长度的孤立块并入相邻块
    KB_MIN_CHUNK_CHARS: int = 30

    # ----- 文档解析（Stage 06-02）-----
    # MinerU HTTP 服务地址（如 http://localhost:8888），为空则跳过 MinerU 解析级
    MINERU_API_URL: str = ""
    # MinerU 解析超时（秒）：大 PDF 解析较慢
    MINERU_TIMEOUT: int = 120
    # 是否启用 Docling 本地解析级（未安装时自动跳过）
    DOCLING_ENABLED: bool = True
    # 知识库管理 API 的临时保护 token（Stage 08 鉴权前的过渡方案），为空则不校验
    KB_ADMIN_TOKEN: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # 忽略 .env 中多余的、未在此声明的变量，避免启动报错
        extra="ignore",
    )

    @model_validator(mode="after")
    def _production_gate(self) -> "Settings":
        """生产配置硬门禁（Stage 13 P0 整改）。

        APP_ENV 为 staging/prod 时逐项校验安全底线，不满足**拒绝启动**
        （fail-fast，一次性列出全部缺项）——此前只有启动 warning，
        默认配置起服务即裸奔（管理面无鉴权/伪向量/弱口令）。
        """
        if self.APP_ENV.lower() not in _PROD_ENVS:
            return self
        problems: list[str] = []
        if not self.AUTH_ENABLED:
            problems.append("AUTH_ENABLED 必须为 true（管理面/聊天面鉴权）")
        if self.DEBUG:
            problems.append("DEBUG 必须为 false")
        if self.EMBEDDING_PROVIDER == "hash" and self.KB_ENABLED:
            problems.append(
                "EMBEDDING_PROVIDER=hash 是开发用伪向量，生产禁用（或 KB_ENABLED=false）"
            )
        if "postgres:postgres@" in self.DATABASE_URL:
            problems.append("DATABASE_URL 使用默认弱口令 postgres:postgres")
        if problems:
            raise ValueError(
                f"生产配置门禁不通过（APP_ENV={self.APP_ENV}）：\n- " + "\n- ".join(problems)
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """获取全局唯一 Settings 实例。

    使用 lru_cache 缓存，保证整个进程内只解析一次配置，
    既是单例又避免重复读取 .env 的开销。
    """
    return Settings()


# 全局配置对象，业务代码统一从这里导入使用
settings = get_settings()
