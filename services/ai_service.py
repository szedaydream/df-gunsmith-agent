# -*- coding: utf-8 -*-
"""
AI 服务：改枪顾问的大脑

三个职责：
1. judge_intent   意图判断：这条消息该反问、该回答，还是闲聊？要不要搜索？
2. get_chat_response  生成回答：注入改枪顾问人设 + 搜索资料 + 防幻觉标注规则
3. 错误处理     和第 4 课一样：分层捕获，优雅降级，绝不把崩溃甩给用户
"""

import logging
import re
from collections import Counter

import openai
from openai import OpenAI

from services.gun_profiles import find_profiles, format_for_prompt
from services.plan_validator import build_fix_instruction, validate_plan

logger = logging.getLogger(__name__)

# 会沉淀进用户画像的偏好字段（枪/距离/指定配件是当次信息，不沉淀）
PROFILE_FIELDS = ("水平", "取向", "预算", "模式")


def format_profile(profile: dict) -> str:
    """画像 dict → 一行注入文本；空画像返回空串（不注入）"""
    items = [f"{k}={v}" for k, v in profile.items() if v]
    return "【玩家画像】" + " ".join(items) if items else ""


def tasks_need_clarify(tasks: list) -> bool:
    """任务清单里有 PLAN 缺模式或距离 → 整回合转入反问（一次问齐，不答任何任务）"""
    for t in tasks:
        if t["type"] == "PLAN":
            s = t["slots"]
            if s.get("模式", "未知") in ("未知", "无", "") \
                    or s.get("距离", "未知") in ("未知", "无", ""):
                return True
    return False


def format_task_list(tasks: list) -> str:
    """任务清单 → 注入生成 prompt 的【本次任务清单】文本（一次生成依次完成）"""
    lines = []
    for i, t in enumerate(tasks):
        s = t["slots"]
        if t["type"] == "PLAN":
            desc = (f"为 {s.get('枪', '?')} 出一张完整方案卡"
                    f"（{s.get('模式', '')} {s.get('距离', '')}）")
        elif t["type"] == "QA":
            desc = f"回答问题：{s.get('问', '?')}（自由作答，不套方案卡格式）"
        elif t["type"] == "COMPARE":
            desc = (f"对比 {s.get('枪', '?')}"
                    f"（关注点：{s.get('关注点', '综合')}），不套方案卡格式")
        else:  # RECOMMEND
            desc = (f"推荐武器（{s.get('模式', '')} {s.get('距离', '')}），"
                    "给候选清单和方向性建议，不套方案卡格式")
        lines.append(f"{i + 1}. {desc}")
    return ("【本次任务清单】玩家一次问了多件事，请依次完成：\n"
            + "\n".join(lines)
            + "\n注意：每个 PLAN 任务各出一张完整方案卡（六段格式），"
              "多把枪时尽量每把枪都搜一次资料；"
              "QA/COMPARE/RECOMMEND 用已有资料清楚作答即可，不受六段格式限制。"
              "资料够就直接写，不要反复宣布「我要再搜索」。")

# ============================================================
# Prompt 1：改枪顾问人设（生成方案时用）
# 三段式：角色 → 规则（含防幻觉红线）→ 输出格式
# ============================================================
GUNSMITH_PROMPT = """你是「三角洲行动」的资深改枪顾问，帮玩家搭配武器配件。语气像懂枪的老朋友：专业、直接、不啰嗦。

【资料与防幻觉】
1. 有【搜索资料】就以资料为准；资料没提到、凭经验补充的配件标「（未核实）」。没附资料时，方案开头写「⚠️ 以下基于通用改枪经验，未经最新资料核实」（反问澄清或闲聊时不写）
2. 每条配件的理由都带来源标注，三选一：搜索查到的标具体来源「（聪聪）」「（NGA）」等；凭经验的标「（未核实）」；由枪械机制推导的标「（推理）」。联网搜索优先参考 bilibili UP主「Always聪聪」，引用他的结论标「（聪聪）」
3. 硬数据（槽位数、配件名、数值）交叉验证：【枪械档案】里有的一律以档案为准；档案没有的需两个独立来源一致才能当事实，只有一个来源标（单一来源），查不到就明说「没查到可靠资料」——宁可少说，绝不说错，说错一个槽位数玩家整套配件就白买了

【需求与定制】
4. 玩家没说清游戏模式（烽火地带/全面战场）或交战距离时，先反问，不给完整方案
5. 玩家明确指定的配件或打法偏好必须尊重，围绕它重新平衡其他槽位，并在【属性评估】说清取舍
6. 【枪械档案】列出的武器特性是改枪第一优先级：方案围绕特性展开，配件理由说清哪个配件服务了特性

【槽位纪律】
7. 槽位以该枪实际为准：方案开头写明共几个配件槽；没有的槽位不列、不编配件；配件会改变槽位数（如长枪管多给护木片槽），装了这类配件要重算槽位
8. 可叠的槽位写清数量（如「游侠护木片 ×2」）；故意空着的槽位标「（空）」并说一句原因；护木片和镭射对属性影响实际很大，枪上有这些槽位别忘了配

【方案输出格式】
【枪械】武器名（共 N 个配件槽）
【定位】模式 + 交战距离
【配件方案】按该枪实际槽位列出
- 槽位名：配件名 —— 一句话理由（来源标注）
【精校建议】每个支持精校的配件给出方向：哪条属性优先拉、拉到哪档。已溢出的属性不加码，低于红线的优先补；查不到具体数值给方向性建议并标（推理）
【属性评估】哪项属性堆得过高溢出、哪项低于实战红线不够用；红线问题明确问玩家能否接受
【使用建议】一两句实战提示
（配件问答、方案对比等非方案类提问，不受此格式限制，直接清楚作答）"""

# ============================================================
# Prompt 2：意图判断 + 信息提取（轻量分类器）
# 关键设计：一行管道分隔的键值对，第一个词是意图标签。
# 教训：7B 级别的小模型输出 JSON 很容易格式出错（实测会脑补字段），
#       管道格式容错高，解析只需 split("|")，坏了也能退回只看第一个词。
# 为什么顺带提取：分类器本来就「看到了」枪名/模式/距离，以前只吐一个单词
#       全扔了，主模型还要从整段历史里重新找一遍——提取一次，两头省钱。
# ============================================================
INTENT_PROMPT = """你是意图分类器兼信息提取器，服务于「三角洲行动改枪顾问」。
判断用户最新消息的意图并提取关键信息，只回复一行，格式：

意图|枪=武器名|模式=游戏模式|距离=交战距离|指定配件=玩家点名要装的配件|取向=改装取向|水平=玩家水平|预算=预算数字

意图四选一：
CLARIFY —— 用户想要改枪方案，但没说清游戏模式（烽火地带/全面战场）或交战距离（近战/中远）
ANSWER  —— 用户想要方案且信息齐全，或询问配件知识、武器对比，或一次改多把枪（枪字段逗号隔开）
MULTI   —— 用户一句话里问了多件「不同类型」的事（如：既要改枪方案、又问配件知识/求推荐/要对比）。
           注意：只有类型混合才算 MULTI；一次改多把枪、连问多个知识问题都是同类型，仍判 ANSWER
CHAT    —— 闲聊、问候、感谢，或与改枪无关的内容

提取规则：
- 枪：消息或历史里提到的武器名，多把用逗号隔开，没有写 无
- 模式：烽火地带 / 全面战场，没提过写 未知
- 距离：近战 / 中近 / 中远 / 远，没提过写 未知
- 指定配件：玩家明确要求装上的配件（如「我要装5倍镜」），没有写 无
- 取向：性价比（预算有限/便宜/穷）/ 满改（不差钱/顶配）/ 激进（猛攻冲脸）/ 稳健（架枪防守）/ 均衡。只在玩家陈述自己的预算或打法时提取；玩家只是询问配件特点时不提取，没提过写 无
- 水平：新手 / 老手。只有句子里出现「我是新手」「我是老手」「我刚入坑」这种「我是X」自我介绍结构才提取；「新手」出现在问号结尾的句子里（「新手适合吗」「新手玩怎么样」）一律写 未知
- 预算：玩家给自己的方案定的预算数字（如 30万）才提取；询问配件价格不是预算，没提写 无
- CHAT 时所有字段写 无

铁律：取向/水平/预算只在玩家「明确陈述自己的情况」时提取——
「我是新手」「我预算 30 万」「我就爱冲脸」是陈述，要提；
「这配件多少钱」「新手适合吗」只是询问，不是陈述，绝不提取。
这条铁律是玩家画像的数据闸门：画像跨对话长期保存，推断出来的
值写进去就是长期污染。

重要：判断「信息是否齐全」和提取字段都要看完整对话历史（含【前情提要】
和【玩家画像】）。画像里已有的字段（如模式）视为玩家已提供——
只要玩家在历史或画像里说过模式和交战距离，就算信息齐全（ANSWER），
绝不允许因为最新一句话没提就重复反问。画像没有的字段照历史提取。

示例（输出照这个格式来，只回一行）：
玩家说「帮我改把 M4A1」→ CLARIFY|枪=M4A1|模式=未知|距离=未知|指定配件=无|取向=无|水平=未知|预算=无
玩家说「预算有限，MP5 烽火地带近战怎么配」→ ANSWER|枪=MP5|模式=烽火地带|距离=近战|指定配件=无|取向=性价比|水平=未知|预算=无
玩家说「我是新手，满改不差钱，整把猛攻的 K416 打全面战场近战」→ ANSWER|枪=K416|模式=全面战场|距离=近战|指定配件=无|取向=满改激进|水平=新手|预算=无
玩家说「SVCH 我想装 5 倍镜打远点，全面战场」→ ANSWER|枪=SVCH|模式=全面战场|距离=远|指定配件=5倍镜|取向=无|水平=未知|预算=无
玩家说「M4A1 烽火地带近战怎么改？另外补偿器和消音器有啥区别」→ MULTI|枪=M4A1|模式=烽火地带|距离=近战|指定配件=无|取向=无|水平=未知|预算=无
玩家说「长枪管多少钱？新手用合适吗」→ ANSWER|枪=无|模式=未知|距离=未知|指定配件=无|取向=无|水平=未知|预算=无
玩家说「K416 怎么样？新手玩合适吗」→ ANSWER|枪=K416|模式=未知|距离=未知|指定配件=无|取向=无|水平=未知|预算=无

只回复这一行，不要输出任何其他内容。"""

# ============================================================
# Prompt 3：多任务拆分器（阶段 2，主模型用——8B 判出 MULTI 后才调用）
# 输出和阶段 1 同一种管道协议：一种格式全系统通用，解析器直接复用。
# 拆分只发生在「理解层」：清单交给一次生成依次完成，不是逐任务独立调用。
# ============================================================
SPLIT_PROMPT = """你是任务拆分器，服务于「三角洲行动改枪顾问」。玩家一句话里问了多件不同类型的事，
把它拆成任务清单，每行一个任务，格式：

任务类型|字段=值|字段=值

任务类型四选一：
PLAN —— 要改枪方案卡。字段：枪/模式/距离/指定配件/取向/水平/预算
QA —— 配件或机制知识问答。字段：问=问题本体
COMPARE —— 武器对比。字段：枪=A,B（逗号分隔）|关注点=对比角度
RECOMMEND —— 求推荐枪。字段：模式/距离/取向/水平/预算（有啥写啥，没有写 无）

规则：
- 最多拆 3 个任务；超过 3 个只保留最相关的 3 个
- 同类型同对象的重复任务合并成一个
- PLAN 的模式/距离：玩家在本次消息或对话历史（含前情提要、玩家画像）里说过就填上，没说过写 未知
- 字段值里不要出现 | 字符
- 只输出任务行，不要任何解释

示例：
玩家说「M4A1 烽火地带近战怎么改？另外补偿器和消音器有啥区别」→
PLAN|枪=M4A1|模式=烽火地带|距离=近战|指定配件=无|取向=无|水平=未知|预算=无
QA|问=枪口补偿器和消音器有什么区别
玩家说「我是新手，求推荐把烽火地带近战枪，顺便对比下 M4 和 AKM 哪个适合我」→
RECOMMEND|模式=烽火地带|距离=近战|取向=无|水平=新手|预算=无
COMPARE|枪=M4A1,AKM|关注点=新手适合哪个"""

TASK_TYPES = ("PLAN", "QA", "COMPARE", "RECOMMEND")
TASK_FIELDS = ("枪", "模式", "距离", "指定配件", "取向", "水平", "预算",
               "问", "关注点")
MAX_TASKS = 3

# ============================================================
# Prompt 4：历史压缩器
# 对话太长时，把旧消息压成「前情提要」——既省 token，
# 又防止陈旧的上下文带偏新话题（比如半小时前聊的枪干扰现在问的枪）
# ============================================================
COMPRESS_PROMPT = """你是对话压缩器，服务于「三角洲行动改枪顾问」。把旧对话压缩成一份前情提要。

保留：玩家提到过的武器、游戏模式、交战距离、明确表达过的配件偏好、
      已给出方案的关键结论（核心配件、属性红线、玩家是否接受）。
丢弃：寒暄、客套、已被推翻的旧需求、与当前话题无关的细节。

输出要点列表，不超过 200 字，直接输出提要本身，不要任何解释。"""


class AIService:
    """AI 服务类：封装大模型调用、意图判断与错误处理"""

    def __init__(self, api_key: str, base_url: str, model: str,
                 use_builtin_search: bool = False,
                 router_api_key: str = "", router_base_url: str = "",
                 router_model: str = ""):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        # Kimi 的内置联网搜索：调用时通过 tools 参数开启
        self.use_builtin_search = use_builtin_search
        # Router 层：意图判断/信息提取这种粗活用便宜小模型，不给贵模型送钱。
        # 没配 router 时回退主模型，行为不变。
        if router_api_key and router_base_url and router_model:
            self.router_client = OpenAI(api_key=router_api_key, base_url=router_base_url)
            self.router_model = router_model
        else:
            self.router_client, self.router_model = self.client, self.model

    # ---------- 内部统一调用入口 ----------
    def _create(self, kwargs: dict, client=None):
        """
        真正发请求的地方（所有出口的唯一出口）。
        统一处理模型参数兼容性：遇到 400 报错里点名的参数，摘掉重试——
        - kimi-k2.6 锁死 temperature=1，拒收其他值
        - 不支持 reasoning_effort 的模型会拒收这个参数
        - 不支持 usage 回传的模型会拒收 stream_options
        """
        client = client or self.client
        for _ in range(3):
            try:
                return client.chat.completions.create(**kwargs)
            except openai.BadRequestError as e:
                msg = str(e)
                dropped = False
                for param in ("temperature", "reasoning_effort", "stream_options"):
                    if param in msg and param in kwargs:
                        kwargs.pop(param)
                        dropped = True
                if not dropped:
                    raise
        raise RuntimeError("参数兼容性重试次数耗尽")

    @staticmethod
    def _is_degenerate(text: str) -> bool:
        """
        复读机检测：同一句子反复出现就算输出退化。
        实测 k2.5 在「必须搜到某个硬数据」的 prompt 下、怎么搜都搜不到时，
        会陷入「让我再搜索一下…」的无限复读——这种输出比报错更害人，
        因为它长得像个回答。
        """
        if not text or len(text) < 200:
            return False
        sents = [s.strip() for s in re.split(r"[。！？!?\n]", text)
                 if len(s.strip()) >= 8]
        if len(sents) < 8:
            return False
        top = Counter(sents).most_common(1)[0][1]
        return top / len(sents) > 0.4

    def _call(self, messages: list, temperature: float = 0.7,
              max_tokens: int = 800, use_search_tool: bool = False,
              use_router: bool = False) -> str:
        """非流式调用（意图判断、健康检查用）。use_router=True 走 router 小模型"""
        kwargs = dict(
            model=self.router_model if use_router else self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort="low"   # 推理模型：压低思考强度换速度（不支持的模型会被 _create 自动摘掉）
        )
        client = self.router_client if use_router else self.client
        if use_search_tool and self.use_builtin_search:
            # Kimi 内置搜索工具：模型自己决定何时联网
            kwargs["tools"] = [{
                "type": "builtin_function",
                "function": {"name": "$web_search"}
            }]

        # 工具调用循环：内置搜索要两个来回——
        # 第 1 次：模型回复 tool_calls（搜索结果已装在 arguments 里，由 Kimi 服务端执行）
        # 第 2 次：我们把工具结果原样回传，模型看着搜索结果生成最终回答
        msg = None
        for _ in range(3):
            resp = self._create(kwargs, client)
            choice = resp.choices[0]
            msg = choice.message
            if choice.finish_reason != "tool_calls" or not getattr(msg, "tool_calls", None):
                content = msg.content or ""
                if self._is_degenerate(content):
                    # 抛异常而不是返回垃圾：各调用方（意图/压缩/生成）都有降级路径
                    raise RuntimeError(f"模型输出复读退化（{content[:40]}…）")
                return content
            # 回传工具结果：assistant 的 tool_calls + 对应的 tool 消息
            kwargs["messages"] = kwargs["messages"] + [
                {"role": "assistant", "content": msg.content or "",
                 "tool_calls": [tc.model_dump() for tc in msg.tool_calls]}
            ] + [
                {"role": "tool", "tool_call_id": tc.id,
                 "content": tc.function.arguments}
                for tc in msg.tool_calls
            ]
        # 3 轮工具调用耗尽、模型还想搜：摘掉工具逼它拿现有资料交卷——
        # 不给这个收尾，它会一直「让我再搜索一下」空转（实测踩过）
        logger.warning("工具调用轮次耗尽，摘除工具强制收尾")
        kwargs.pop("tools", None)
        kwargs["messages"] = kwargs["messages"] + [
            {"role": "assistant", "content": msg.content or "",
             "tool_calls": [tc.model_dump() for tc in msg.tool_calls]}
        ] + [
            {"role": "tool", "tool_call_id": tc.id,
             "content": tc.function.arguments}
            for tc in msg.tool_calls
        ] + [
            {"role": "user",
             "content": "搜索次数已用完。请根据已搜到的资料直接作答；"
                        "资料里确实没有的，就明说「没查到可靠资料」，不要编。"}
        ]
        content = self._create(kwargs, client).choices[0].message.content or ""
        if self._is_degenerate(content):
            raise RuntimeError("强制收尾后输出仍复读退化")
        return content

    @staticmethod
    def _friendly_error(e: Exception) -> str:
        """分层错误处理：具体异常具体话术，未知异常兜底话术（第 4 课的原则）"""
        if isinstance(e, openai.AuthenticationError):
            logger.error("❌ API 密钥认证失败")
            return "😔 系统配置异常，请联系管理员检查密钥"
        if isinstance(e, openai.RateLimitError):
            logger.error("❌ 触发限流")
            return "😔 当前使用人数较多，请稍后再试"
        if isinstance(e, (openai.APITimeoutError, openai.APIConnectionError)):
            logger.error("❌ 网络连接/超时")
            return "😔 网络有点不稳定，请稍后再试"
        logger.error(f"❌ AI 服务异常: {e}")
        return "😔 我遇到了一点技术问题，请稍后再试"

    # ---------- 职责 1：意图判断 + 信息提取 ----------
    @staticmethod
    def _parse_intent(raw: str) -> dict:
        """
        解析分类器的一行输出：意图|枪=X|模式=Y|距离=Z|指定配件=W
        容错原则：第一个词决定意图（大写后查关键词，和老版一致）；
        后面的字段解析失败就丢，绝不让格式瑕疵影响意图判断。
        """
        parts = raw.strip().split("|")
        head = parts[0].upper()
        if "CLARIFY" in head:
            action = "clarify"
        elif "CHAT" in head:
            action = "chat"
        elif "MULTI" in head:
            action = "multi"
        else:
            action = "answer"   # 包含 ANSWER 或回复异常时，默认回答
        slots = {}
        for seg in parts[1:]:
            key, sep, value = seg.partition("=")
            if sep and key.strip() in ("枪", "模式", "距离", "指定配件",
                                       "取向", "水平", "预算"):
                slots[key.strip()] = value.strip()
        return {"action": action, "need_search": action == "answer", "slots": slots}

    @staticmethod
    def split_tasks(raw: str) -> list:
        """
        解析阶段 2 的任务清单（管道多行，每行一个任务）。
        容错和 _parse_intent 同款：行内坏字段丢弃，整行不像任务的跳过，
        绝不抛异常——解析不出来的行是噪音，不是错误。
        返回 [{"type": "PLAN"|"QA"|"COMPARE"|"RECOMMEND", "slots": {...}}, ...]
        """
        tasks = []
        for line in raw.strip().splitlines():
            parts = line.strip().split("|")
            head = parts[0].strip().upper()
            if head not in TASK_TYPES:
                continue   # 解释性文字/空行/坏行：丢
            slots = {}
            for seg in parts[1:]:
                key, sep, value = seg.partition("=")
                if sep and key.strip() in TASK_FIELDS:
                    slots[key.strip()] = value.strip()
            if head == "PLAN" and not slots.get("枪"):
                continue   # 方案任务连枪都没有，是废行
            if head == "QA" and not slots.get("问"):
                continue
            tasks.append({"type": head, "slots": slots})
        return tasks[:MAX_TASKS]

    def judge_intent(self, messages: list, profile: dict = None) -> dict:
        """
        轻量意图分类 + 关键信息提取（枪/模式/距离/指定配件）。
        messages 是路由层裁好的窗口（前情提要 + 最近 6 条），全量传给分类器——
        之前只传 [-4:]，把提要和早前说过的话弄丢了，模型看不到玩家上次说过的
        模式/距离，就会重复反问。
        profile 是跨对话的玩家画像（水平/取向/预算/模式），作为 system 消息
        插在历史前面——画像里已有的字段视为玩家已提供，不重复反问。
        返回示例：{"action": "answer", "need_search": True,
                  "slots": {"枪": "K437", "模式": "烽火地带", "距离": "中近"}}
        """
        try:
            prompt_msgs = [{"role": "system", "content": INTENT_PROMPT}]
            if profile:
                prompt_msgs.append({"role": "system",
                                    "content": format_profile(profile)})
            raw = self._call(
                prompt_msgs + messages,
                temperature=0, max_tokens=800,   # 推理模型：思考过程也吃 max_tokens，给 20 会全被吃掉
                use_router=True                  # 粗活走 router 小模型，不给贵模型送钱
            )
            result = self._parse_intent(raw)
            logger.info(f"🧭 意图判断: {result['action']} slots={result['slots']}"
                        f"（原始回复: {raw.strip()[:60]}）")

            # 阶段 2：8B 判出异构混合（MULTI），让主模型拆成任务清单。
            # 拆分很贵（一次主模型调用），所以只在 MULTI 时触发——
            # 纯多枪不走这里（枪字段逗号隔开，生成端直接逐枪出卡）
            if result["action"] == "multi":
                tasks = self._split_into_tasks(messages, profile)
                if not tasks:
                    logger.warning("阶段 2 拆不出任务，降级为单任务回答")
                    result = {"action": "answer", "need_search": True,
                              "slots": result["slots"]}
                elif tasks_need_clarify(tasks):
                    # 有 PLAN 缺模式/距离：回合级反问，一次问齐，不答任何任务
                    logger.info(f"🧭 多任务但信息不全，转反问: {tasks}")
                    result = {"action": "clarify", "need_search": False,
                              "slots": result["slots"], "tasks": tasks}
                else:
                    logger.info(f"🧭 任务拆分: {tasks}")
                    result = {"action": "multi", "need_search": True,
                              "slots": result["slots"], "tasks": tasks}
            # 交叉验证：提取出的枪名和本地档案匹配对一下——
            # 档案库里查无此枪，说明分类器可能听错了（或真是新枪），记日志留线索
            gun = result["slots"].get("枪", "")
            if gun and gun != "无":
                known = set(find_profiles(gun).keys())
                for g in gun.split("，") + gun.split(","):
                    g = g.strip()
                    if g and not any(g.lower() in k.lower() or k.lower() in g.lower()
                                     for k in known):
                        logger.info(f"⚠️ 提取的枪名「{g}」未命中档案库")
            return result
        except Exception as e:
            # 分类失败不能阻塞主流程：按「直接回答」处理最保险
            logger.warning(f"意图判断失败，降级为直接回答: {e}")
            return {"action": "answer", "need_search": True, "slots": {}}

    def _split_into_tasks(self, messages: list, profile: dict = None) -> list:
        """
        阶段 2：把异构混合的一句话拆成任务清单（主模型干——拆分错了
        后面全错，这钱不能省；但只在 MULTI 时调用，常态不花这笔钱）。
        输出和阶段 1 同一种管道协议，解析复用 split_tasks。
        """
        prompt_msgs = [{"role": "system", "content": SPLIT_PROMPT}]
        if profile:
            prompt_msgs.append({"role": "system",
                                "content": format_profile(profile)})
        raw = self._call(prompt_msgs + messages,
                         temperature=0, max_tokens=800)
        return self.split_tasks(raw)

    # ---------- 职责 2：生成回答 ----------
    def _build_system_prompt(self, messages: list, search_results: list,
                             force_clarify: bool, slots: dict = None,
                             profile: dict = None, tasks: list = None) -> str:
        """
        拼装 system prompt：人设 + 可选的几块「补丁」。
        注意顺序是缓存友好的：GUNSMITH_PROMPT 恒定不变，永远在最前面；
        会变的补丁（任务/需求/画像/档案/资料）一律追加在后面。
        Kimi 的 Context Caching 是自动前缀缓存——前缀逐 token 一致才命中，
        所以不要在 GUNSMITH_PROMPT 前面插任何随请求变化的内容。
        force_clarify：意图判断为 clarify 时追加硬指令。为什么要这个开关？
            实测 GUNSMITH_PROMPT 里虽然写了「信息不全先反问」，
            但模型经常忍不住直接给方案——通用规则管常态，硬指令管本次。
        slots：意图阶段提取出的关键信息，直接告诉主模型，省得它去历史里翻。
        profile：跨对话的玩家画像（水平/取向/预算/模式），优先级低于
            本次消息里明说的一切——画像只补缺，不抢戏。
        tasks：多任务清单（① 多意图拆分）。理解层拆分、一次生成：
            清单注入后模型一口气依次完成，成本/延迟和单任务一样。
        """
        system_prompt = GUNSMITH_PROMPT
        if force_clarify:
            system_prompt += ("\n\n【本次任务】玩家没说清游戏模式或交战距离，"
                              "本次只反问澄清这两点，不要给任何配件方案。"
                              "但如果对话历史（含前情提要和玩家画像）里玩家已经说过"
                              "模式和交战距离，视为信息齐全，直接给方案，禁止重复反问。"
                              "画像里已有的字段不要再次反问。")
            if tasks:
                system_prompt += ("\n玩家这次一次问了多件事，把这些任务缺少的"
                                  "模式/距离一次问齐（可以问「都是同一个模式和"
                                  "距离吗，还是各不一样」）：\n"
                                  + format_task_list(tasks))

        if tasks and not force_clarify:
            system_prompt += "\n\n" + format_task_list(tasks)

        if slots:
            brief = "；".join(f"{k}：{v}" for k, v in slots.items()
                              if v and v not in ("无", "未知"))
            if brief:
                system_prompt += (f"\n\n【玩家需求提取】{brief}。"
                                  "这是从对话里提取好的关键信息，方案直接按此出，"
                                  "不要就这些信息重复反问。")

        if profile:
            system_prompt += (
                f"\n\n{format_profile(profile)}"
                "这是这位玩家长期保存的偏好。玩家本次消息里明说的信息优先于画像；"
                "没说的部分按画像补齐（如取向、水平），并体现在方案取舍里。")

        # 枪械档案命中：人工核对过的地面真相，优先级高于搜索。
        # 槽位布局、特性这类硬事实只认档案——模型凭印象或搜来的都可能是错的
        user_text = " ".join(m["content"] for m in messages if m.get("role") == "user")
        profiles = find_profiles(user_text)
        if profiles:
            system_prompt += (
                "\n\n【枪械档案】（人工核对数据，最高优先级：槽位布局、数量、特性以档案为准；"
                "与搜索资料冲突时听档案的。档案标了「待核对」的条目，引用时标注（档案待核对）。"
                "档案没收录的数据按规则 3 交叉验证）\n"
                + format_for_prompt(profiles)
            )
            logger.info(f"📚 命中枪械档案: {list(profiles.keys())}")

        if search_results:
            # 把搜索资料拼进 system prompt——这就是「搜索增强」的全部秘密
            material = "\n".join(
                f"{i + 1}. {r['title']}：{r['snippet']}"
                for i, r in enumerate(search_results)
            )
            system_prompt += f"\n\n【搜索资料】\n{material}"
        elif self.use_builtin_search:
            # 内置搜索模式：资料是模型自己联网查的，不走【搜索资料】注入。
            # 必须跟模型讲清楚「你自己搜到的也算资料」，否则它会严格按规则 2
            # 把每个配件都标成（未核实）——实测全标，方案没法用。
            # 注意措辞是「必须搜索」不是「可以搜索」：写"可以"模型会偷懒不搜，
            # 凭经验直接答，方案照样满屏（未核实）。
            system_prompt += (
                "\n\n【搜索方式说明】本次对话你配备了联网搜索工具。凡是出改枪方案或"
                "配件推荐，必须先调用搜索查最新版本攻略，再动笔写方案——不允许不搜索"
                "直接凭经验回答。凡是你通过搜索查到的信息，一律视为已核实的资料，"
                "直接引用、不要标注「（未核实）」；只有搜过也没查到、凭你自身经验补充的"
                "配件才标注「（未核实）」。"
                "搜索关键词建议组合：「三角洲行动 + 武器名 + 改枪」「bilibili Always聪聪 + 武器名」。"
                "采信优先级：Always聪聪的视频结论 > 官方公告/ NGA 精华帖等社区高可信来源 > 一般资讯站。"
            )
        return system_prompt

    def get_chat_response(self, messages: list, search_results: list = None,
                          force_clarify: bool = False, slots: dict = None,
                          profile: dict = None, tasks: list = None) -> str:
        """非流式生成（保留给测试和调试用；线上走 get_chat_response_stream）"""
        system_prompt = self._build_system_prompt(messages, search_results,
                                                  force_clarify, slots, profile,
                                                  tasks)
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        try:
            return self._call(full_messages, max_tokens=6000,
                              use_search_tool=(search_results is None))
        except Exception as e:
            return self._friendly_error(e)

    def get_chat_response_stream(self, messages: list, search_results: list = None,
                                 force_clarify: bool = False,
                                 require_search: bool = False,
                                 slots: dict = None,
                                 profile: dict = None,
                                 tasks: list = None):
        """
        流式生成（生成器）。逐段 yield 事件 dict，前端边收边渲染：
            {"type": "status",   "text": "..."}   状态提示（如：正在联网搜索）
            {"type": "thinking", "delta": "..."}  思考过程片段（reasoning）
            {"type": "answer",   "delta": "..."}  正文片段
            {"type": "error",    "text": "..."}   出错（友好话术）
        设计要点：内置搜索的第 1 轮用「非流式」发——那轮模型只回 tool_calls
        （一句话正文都没有），流式没意义；第 2 轮才开流式，思考和正文才真正流出。
        require_search：answer 意图时置 True。实测模型会偷懒——prompt 写了
        「必须先搜索」也偶尔不搜，凭经验直接答，方案满屏（未核实）。
        所以后端再兜一道：第 1 轮没调搜索工具，就追加硬指令让它重搜。
        """
        system_prompt = self._build_system_prompt(messages, search_results,
                                                  force_clarify, slots, profile,
                                                  tasks)
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        # max_tokens 给足：推理模型的思考也吃额度，实测 6000 都会被长思考吃光导致正文为空
        # reasoning_effort=low：压低思考强度，等待时间从几分钟降到几十秒
        kwargs = dict(model=self.model, messages=full_messages,
                      temperature=0.7, max_tokens=16000, reasoning_effort="low")

        # 请求级追踪：这一趟发生了什么（搜了几轮/有没有被打回重搜/质检结果/
        # token 花了多少），流结束时以 trace 事件抛给调用方落库——
        # 排障时查库就行，不用翻控制台
        trace = {"search_rounds": 0, "forced_research": False,
                 "validation_issues": [], "fix_retried": False,
                 "prompt_tokens": 0, "completion_tokens": 0,
                 "search_queries": []}   # 模型实际搜了什么（多任务时查覆盖用）

        def add_usage(resp_or_chunk):
            """把一次响应的 token 用量累加进 trace（有的模型不回 usage，容错）"""
            u = getattr(resp_or_chunk, "usage", None)
            if u:
                trace["prompt_tokens"] += getattr(u, "prompt_tokens", 0) or 0
                trace["completion_tokens"] += getattr(u, "completion_tokens", 0) or 0

        try:
            # 第 1 轮（仅内置搜索模式）：非流式，看模型要不要联网
            if search_results is None and self.use_builtin_search:
                # 先发状态再发请求——第 1 轮要等模型思考+服务端搜完才返回，
                # 是全程最长的一段静默期，不提前打招呼用户会以为卡死了
                yield {"type": "status", "text": "🔍 正在联网搜索资料（约半分钟）…"}
                kwargs["tools"] = [{
                    "type": "builtin_function",
                    "function": {"name": "$web_search"}
                }]
                resp = self._create(kwargs)
                add_usage(resp)
                msg = resp.choices[0].message
                if getattr(msg, "tool_calls", None):
                    trace["search_rounds"] += 1
                    trace["search_queries"] += [tc.function.arguments[:100]
                                                for tc in msg.tool_calls]
                    kwargs["messages"] = kwargs["messages"] + [
                        {"role": "assistant", "content": msg.content or "",
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]}
                    ] + [
                        {"role": "tool", "tool_call_id": tc.id,
                         "content": tc.function.arguments}
                        for tc in msg.tool_calls
                    ]
                    yield {"type": "status", "text": "📖 资料到手，正在写方案…"}
                elif require_search:
                    # 该搜没搜：把模型直接答的内容丢掉，追加硬指令逼它重搜
                    logger.warning("模型未搜索直接作答，追加硬指令重试")
                    trace["forced_research"] = True
                    yield {"type": "status", "text": "🔍 要求顾问必须先查资料，正在补搜…"}
                    kwargs["messages"] = kwargs["messages"] + [
                        {"role": "assistant", "content": msg.content or ""},
                        {"role": "user",
                         "content": "停。你刚才没有联网搜索。这是改枪方案类问题，"
                                    "规则要求必须先调用搜索工具查最新攻略再回答。"
                                    "现在请调用搜索工具。"}
                    ]
                    resp = self._create(kwargs)
                    add_usage(resp)
                    msg = resp.choices[0].message
                    if getattr(msg, "tool_calls", None):
                        trace["search_rounds"] += 1
                        trace["search_queries"] += [tc.function.arguments[:100]
                                                    for tc in msg.tool_calls]
                        kwargs["messages"] = kwargs["messages"] + [
                            {"role": "assistant", "content": msg.content or "",
                             "tool_calls": [tc.model_dump() for tc in msg.tool_calls]}
                        ] + [
                            {"role": "tool", "tool_call_id": tc.id,
                             "content": tc.function.arguments}
                            for tc in msg.tool_calls
                        ]
                        yield {"type": "status", "text": "📖 资料到手，正在写方案…"}
                    elif msg.content:
                        # 重试还是不搜：认栽放行（有 prompt 规则 2 的声明兜底）
                        yield {"type": "answer", "delta": msg.content}
                        yield {"type": "trace", "data": trace}
                        return
                elif msg.content:
                    # 闲聊/反问路径：模型没调搜索直接答完了。
                    # 兜底检查：如果「只许反问」的回合里它偷跑了完整方案，
                    # 这份方案必是无资料瞎编——丢掉，按 answer 流程打回逼它先搜
                    if force_clarify and "【配件方案】" in msg.content:
                        logger.warning("clarify 回合偷跑方案，打回重搜")
                        trace["forced_research"] = True
                        yield {"type": "status", "text": "🔍 方案缺资料支撑，正在补搜…"}
                        kwargs["messages"] = kwargs["messages"] + [
                            {"role": "assistant", "content": msg.content},
                            {"role": "user",
                             "content": "停。你刚才直接给了方案但没有联网搜索。"
                                        "规则要求出方案前必须先调用搜索工具查最新攻略。"
                                        "现在请调用搜索工具。"}
                        ]
                        resp = self._create(kwargs)
                        add_usage(resp)
                        msg = resp.choices[0].message
                        if getattr(msg, "tool_calls", None):
                            trace["search_rounds"] += 1
                            trace["search_queries"] += [tc.function.arguments[:100]
                                                        for tc in msg.tool_calls]
                            kwargs["messages"] = kwargs["messages"] + [
                                {"role": "assistant", "content": msg.content or "",
                                 "tool_calls": [tc.model_dump() for tc in msg.tool_calls]}
                            ] + [
                                {"role": "tool", "tool_call_id": tc.id,
                                 "content": tc.function.arguments}
                                for tc in msg.tool_calls
                            ]
                            yield {"type": "status", "text": "📖 资料到手，正在写方案…"}
                        elif msg.content:
                            yield {"type": "answer", "delta": msg.content}
                            yield {"type": "trace", "data": trace}
                            return
                    else:
                        # 正常闲聊/反问：正文一次性发完
                        yield {"type": "answer", "delta": msg.content}
                        yield {"type": "trace", "data": trace}
                        return
            else:
                yield {"type": "status", "text": "✍️ 正在写方案…"}

            # 第 2 轮起（或无搜索的唯一一轮）：流式，思考与正文逐段流出。
            # 但模型搜完一轮后可能还想再搜（比如先搜通用攻略、再搜聪聪专项）——
            # 流式回合的 finish_reason=tool_calls 必须接住，否则用户只看到
            # 一句「我再搜索一下…」就没有下文（G4 实测踩过这个坑）。
            for _ in range(3):
                # include_usage：让流式的最后一帧带上 token 用量（不支持的模型
                # 会被 _create 自动摘掉这个参数，行为不变）
                stream = self._create(dict(kwargs, stream=True,
                                           stream_options={"include_usage": True}))
                pending_calls = {}   # index -> 累积中的 tool_call 碎片
                round_content = []   # 本轮已流出的正文（回传 assistant 消息时要带上）
                finish = None
                for chunk in stream:
                    add_usage(chunk)   # usage 帧没有 choices，要在 continue 之前接
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    finish = choice.finish_reason or finish
                    delta = choice.delta
                    # 推理模型的思考过程在 reasoning_content 字段里
                    thinking = getattr(delta, "reasoning_content", None)
                    if thinking:
                        yield {"type": "thinking", "delta": thinking}
                    if delta.content:
                        round_content.append(delta.content)
                        yield {"type": "answer", "delta": delta.content}
                    # 流式的 tool_calls 是碎片化的，按 index 拼完整
                    for tc in (getattr(delta, "tool_calls", None) or []):
                        slot = pending_calls.setdefault(
                            tc.index, {"id": "", "name": "", "args": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function:
                            slot["name"] = slot["name"] or (tc.function.name or "")
                            slot["args"] += tc.function.arguments or ""
                if finish != "tool_calls" or not pending_calls or "tools" not in kwargs:
                    break   # 正常答完（或闲聊回合），收工
                # 模型要再搜一轮：把已流出的正文+tool_calls 回传，工具结果照旧回塞
                trace["search_rounds"] += 1
                calls = [pending_calls[i] for i in sorted(pending_calls)]
                trace["search_queries"] += [c["args"][:100] for c in calls]
                yield {"type": "status", "text": "🔍 顾问在补充搜索更多资料…"}
                kwargs["messages"] = kwargs["messages"] + [
                    {"role": "assistant", "content": "".join(round_content),
                     "tool_calls": [{"id": c["id"], "type": "function",
                                     "function": {"name": c["name"],
                                                  "arguments": c["args"]}}
                                    for c in calls]}
                ] + [
                    {"role": "tool", "tool_call_id": c["id"], "content": c["args"]}
                    for c in calls
                ]

            # 出厂质检：方案卡过一遍规则校验（结构/槽位/标注/红线），
            # 不合格把问题清单喂回模型自动修正一次。零额外模型调用做检查，
            # 只在不合格时才花一次修正的生成
            final_text = "".join(round_content)
            if "【配件方案】" in final_text:
                user_text = " ".join(m["content"] for m in messages
                                     if m.get("role") == "user")
                issues = validate_plan(final_text, find_profiles(user_text))
                trace["validation_issues"] = issues
                if issues:
                    trace["fix_retried"] = True
                    logger.warning(f"🧪 方案质检未过（{len(issues)} 处）: {issues}")
                    yield {"type": "status",
                           "text": f"🧪 质检发现 {len(issues)} 处问题，正在自动修正…"}
                    yield {"type": "answer",
                           "delta": "\n\n---\n🧪 **初稿未过质检，以下是修正版：**\n\n"}
                    fix_kwargs = dict(kwargs)
                    fix_kwargs.pop("tools", None)   # 修正不用再搜，资料都在上下文里
                    fix_kwargs["messages"] = kwargs["messages"] + [
                        {"role": "assistant", "content": final_text},
                        {"role": "user", "content": build_fix_instruction(issues)},
                    ]
                    stream = self._create(dict(fix_kwargs, stream=True,
                                               stream_options={"include_usage": True}))
                    for chunk in stream:
                        add_usage(chunk)
                        if not chunk.choices:
                            continue
                        delta = chunk.choices[0].delta
                        thinking = getattr(delta, "reasoning_content", None)
                        if thinking:
                            yield {"type": "thinking", "delta": thinking}
                        if delta.content:
                            yield {"type": "answer", "delta": delta.content}
                else:
                    logger.info("🧪 方案质检通过")
            yield {"type": "trace", "data": trace}
        except Exception as e:
            yield {"type": "error", "text": self._friendly_error(e)}
            yield {"type": "trace", "data": {**trace, "error": str(e)}}

    # ---------- 职责 3：历史压缩 ----------
    def compress_history(self, new_old_messages: list,
                         existing_summary: str = "") -> str:
        """
        滚动压缩：把「已有摘要 + 这批新变旧的消息」压成一份新摘要。
        只传增量消息（上次没压缩过的），不每次从头压全量——省钱也省时。
        压缩失败时降级：返回旧摘要，主流程不受影响。
        """
        lines = [f"{'玩家' if m['role'] == 'user' else '顾问'}：{m['content']}"
                 for m in new_old_messages]
        material = "\n".join(lines)
        user_content = (
            f"【已有前情提要】\n{existing_summary or '（无）'}\n\n"
            f"【新增旧对话】\n{material}\n\n"
            f"请合并压缩成一份新的前情提要。"
        )
        try:
            summary = self._call(
                [{"role": "system", "content": COMPRESS_PROMPT},
                 {"role": "user", "content": user_content}],
                max_tokens=600
            )
            logger.info(f"🗜️ 历史压缩：{len(new_old_messages)} 条旧消息 → 摘要 {len(summary)} 字")
            return summary
        except Exception as e:
            logger.warning(f"历史压缩失败，沿用旧摘要: {e}")
            return existing_summary

    def test_connection(self) -> bool:
        """健康检查：发一句你好，拿到非错误话术就算通"""
        try:
            # max_tokens 不能给小：推理模型的思考过程也占额度。
            # 实测 kimi-k3 用 20 时 reasoning_tokens 就吃掉全部 20，正文为空，
            # 于是 bool('') == False → 把"正常"误判成"异常"。
            reply = self._call([{"role": "user", "content": "你好"}], max_tokens=200)
            ok = bool(reply) and not reply.startswith("😔")
            logger.info("✅ AI 服务连接正常" if ok else "⚠️ AI 服务异常")
            return ok
        except Exception as e:
            logger.error(f"❌ 连接测试失败: {e}")
            return False
