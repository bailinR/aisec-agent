from typing import List, Dict, Any, Optional
from typing import Literal

from pydantic import BaseModel, Field


class DocumentAnalysisResultDetailed(BaseModel):
    """文档分析结果（简化版本）"""

    doc_type: Literal[1, 2, 3, 4, 5, 6, 7] = Field(
        ...,
        description="文档类型：1=日志，2=报告，3=资产表，4=代码，5=介绍，6=问答，7=教程"
    )

    class SliceConfig(BaseModel):
        split_pattern: str = Field(
            ...,
            description="用于分割文档的符号或正则表达式，例如：'\\n\\n'或'[。！？]'"
        )
        chunk_size: int = Field(
            ...,
            ge=50,
            le=1000,
            description="每个文本块的最大字符数"
        )
        use_regex: bool = Field(
            False,
            description="是否使用正则表达式进行分割"
        )

    slice_config: SliceConfig


class SQLQueryResult(BaseModel):
    """SQL查询生成结果"""
    sql: str = Field(
        ...,
        description="生成的SELECT语句，不包含换行符和转义符号，所有表头使用AS转换为中文别名"
    )

    explanation: str = Field(
        ...,
        description="SQL语句的详细解释，说明查询逻辑和字段选择原因"
    )

    query_type: Literal[
        "direct_sql_query_table",
        "direct_sql_query_count"
    ] = Field(
        ...,
        description="查询函数类型：direct_sql_query_table(表格查询)、direct_sql_query_count(计数查询)"
    )


class SelectHRTable(BaseModel):
    selected_tables: List[str] = Field(..., description="列表中的元素为表JSON中的key,也就是表名")
    reason: str = Field(..., description="选择该表的原因")


class QueryHRTable(BaseModel):
    sql: str = Field(..., description="SQL server查询语句,必须根据提供的表结构生成")
    reason: str = Field(..., description="对输出sql的解释")


class EsQueryModel(BaseModel):
    """
    用于解析和验证大模型生成的Elasticsearch查询方案的Pydantic模型。
    """
    index: str = Field(..., description="要查询的ES索引名")
    # query: Dict[str, Any] = Field(..., description="Elasticsearch的查询DSL,注意：这是7.x以上版本的新写法，这里query参数对应的是query DSL部分（即不需要再写一层query）")
    reason: str = Field(..., description="AI选择该索引和字段的理由说明")
    # source: List[str] = Field(..., description="需要返回的字段列表。根据用户的问题,只返回需要的字段")
    # aggs: Optional[Dict[str, Any]] = Field(None, description="聚合操作的定义，如有聚合需求则填写，否则为None。生成的聚合字段不要自动加 .keyword 后缀，只有在做 term/group by 时才加")


class RectifyPromptModel(BaseModel):
    conform: bool = Field(..., description="查询结果是否符合用户问题, 符合为true, 不符合为")
    reason: str = Field(..., description="原因")


class HRPromptModel(BaseModel):
    nb: int = Field(..., description="数据集描述的序号")


class HRSplitPromptModel(BaseModel):
    title: list[str] = Field(..., description='需要保留的字段名列表。例如：["年度", "当年离退休总数"]')
    filter: list[str] = Field(..., description='需要过滤的条件列表，每个条件为一个字符串表达式。例如：["年度==2024", "医生>0"]')
    desc: str = Field(..., description='简要说明你的清洗思路')
    agg: dict = Field(..., description='数据聚合方式')


class ParseFirewallLogModel(BaseModel):
    firewallLog: bool = Field(..., description="用户问题是否需要解析日志.true(是)/false(不是)")
    startDate: str = Field(..., description="开始时间,格式yyyy-MM-dd")
    endDate: str = Field(..., description="结束时间,格式yyyy-MM-dd")
    ip: str = Field(..., description="用户问题中指定的ip, 格式为xxx.xxx.xxx.xxx")


class MCPBaseModel(BaseModel):
    reason: str = Field(..., description="原因")


class SelectMCPModel(MCPBaseModel):
    tool_name: str = Field(..., description="工具名称")


class CallMCPModel(MCPBaseModel):
    arguments: dict = Field(..., description="工具参数,填充到此参数下")


class DismantleInfoModel(MCPBaseModel):
    info: dict = Field(..., description="从用户问题中拆解信息填充到这个参数中")


class MakeCorpusModel(BaseModel):
    corpus: list[str] = Field(..., description="语料库列表")


class DecoTaskBase(BaseModel):
    """
    定义每个任务步骤的具体内容和预期格式。
    """
    step: str = Field(..., description="步骤内容")
    remark: str = Field(..., description="说明如何完成此步骤")
    format: Literal["html", "markdown", "code", "table", "text"] = Field(
        ..., description="预期输出内容格式 (html/markdown/code/table/text)"
    )


class DecoTaskModel(BaseModel):
    steps: list[DecoTaskBase] = Field(..., description="步骤列表")


class StepSelectToolModel(BaseModel):
    tools: list[str] = Field(..., description="完成步骤所需的mcp工具列表")


class DuckTaskModel(BaseModel):
    sql: str = Field(..., description="duck db sql 语句")

class HtmlTaskModel(BaseModel):
   html: str = Field(..., description="html 格式化后结果")


class MarkdownOutputModel(BaseModel):
    markdown: str = Field(..., description="格式化后结果")

class MemoryModel(BaseModel):
    text: str = Field(..., description="简化后的文本")

class SessionRAGAnswerModel(BaseModel):
    answer: str = Field(..., description="Final answer for the user")
    enough_info: bool = Field(..., description="Whether the current context is enough to answer")
    missing_info: str = Field("", description="Information still needed when enough_info is false")
    used_knowledge: list[str] = Field(default_factory=list, description="Knowledge snippets used in the answer")


class ProjectDocumentSelectionModel(BaseModel):
    document_ids: list[str] = Field(
        default_factory=list,
        description="Project document ids that should be read for the latest user input",
    )
    reason: str = Field("", description="Brief reason for the document selection")


class TransformModel(BaseModel):
    translation: str = Field(..., description="翻译结果")
    remark: Optional[str]  = Field("", description="术语说明")


class VoiceLLMInputForm(BaseModel):
    speaker1: str = Field(..., description='说话人1 讲述内容')
    speaker2: str = Field(..., description='说话人2 讲述内容')


class VoiceLLMInputFormTwo(VoiceLLMInputForm):
    data: list[VoiceLLMInputForm] = Field(..., description='两人对话内容')


class JSTaskModel(BaseModel):
    js: str = Field(..., description="使用PptxGenJS库生成PowerPoint演示文稿的JavaScript代码")


class PPTSection(BaseModel):
    title: str
    text: Optional[str]
    items: Optional[list[str]]
    type: Optional[str]


class coverSection(BaseModel):
    title: str = Field(..., description="ppt标题")
    text: str = Field(..., description="内容概括")


class CoverSection(BaseModel):
    data: coverSection = Field(description="PPT 页面内容")
    typ: str = Field(default="cover", alias="type", description="固定常量 cover")


class _mulu(BaseModel):
    items: list[str] = Field(..., description="目录标题,如章节标题1, 章节标题2, 章节标题3")


class MuluSection(BaseModel):
    data: _mulu = Field(description="PPT所有章节内容")
    type: str = Field(default="contents", description="固定常量 contents")


class transition(BaseModel):
    title: str = Field(..., description="章节标题")
    text: str = Field(..., description="内容概括 不超过100字")


class transitionSection(BaseModel):
    data: transition = Field(..., description="章节过渡页 内容")
    type: str = Field(default="transition", description="固定常量")


class _itemSection(BaseModel):
    title: str = Field(..., description="节的名字")
    text: str = Field(..., description="节的对应的内容")


class itemSection(BaseModel):
    title: str = Field(..., description="章节标题")
    data: list[_itemSection] = Field(description="ppt该内所有小节内容")


class ItemSection(BaseModel):
    data: itemSection = Field(description="章节内容")
    type: str = Field(default="contents", description="固定常量")


class SectionsModel(BaseModel):
    transition: transitionSection = Field(description="章节页")
    items: ItemSection = Field(description="内容页")


class PPTSectionList(BaseModel):
    cover: CoverSection = Field(..., description="ppt 封面页")
    mulu: MuluSection = Field(..., description="ppt目录页")
    res: List[SectionsModel] = Field(..., description="ppt 正文 包含多个章节内容")


class PPTGenerateRespond(BaseModel):
    title: str
    data: PPTSection


class PdfModifyRequest(BaseModel):
    bucket: str = Field(description="bucket")
    path: str = Field(description="path")


class PdfSplitRequest(PdfModifyRequest):
    split_rages: list[tuple[int, int]]


class PdfMergesRequest(BaseModel):
    bucket: str = Field(description="bucket")
    path: list[str] = Field(description="路径列表")


class PdfDeletePagesRequest(PdfModifyRequest):
    pages: list[int]


class PdfRotatePagesRequest(PdfModifyRequest):
    pages: list[int]
    angle: int




class MarkdownValueMixin:
    def to_markdown(self) -> str:
        """
        把字段的 description 作为 label，把实例的值按 Markdown 格式输出。
        支持嵌套 BaseModel、list、dict。
        """
        def render(val: Any, indent: int = 0) -> str:
            pad = "  " * indent
            if isinstance(val, BaseModel):
                return val.to_markdown()
            if isinstance(val, list):
                lines = []
                for item in val:
                    if isinstance(item, BaseModel):
                        # 嵌套模型自动缩进
                        nested = item.to_markdown().splitlines()
                        lines.append(pad + "- " + nested[0])
                        for nl in nested[1:]:
                            lines.append(pad + "  " + nl)
                    else:
                        lines.append(pad + f"- {item}")
                return "\n".join(lines)
            if isinstance(val, dict):
                lines = []
                for k, v in val.items():
                    lines.append(pad + f"- **{k}**: {v}")
                return "\n".join(lines)
            return pad + str(val)

        parts: List[str] = []
        for name, field in self.__fields__.items():
            desc = field.field_info.description or name
            val = getattr(self, name)
            parts.append(f"**{desc}**:")
            parts.append(render(val, indent=1))
            parts.append("")  # 空行
        return "\n".join(parts).rstrip()


class ProcessingStep(MarkdownValueMixin, BaseModel):
    step: str = Field(..., description="处理步骤名称")
    actions: Optional[List[str]] = Field(None, description="本步骤执行的动作列表")
    modules: Optional[Dict[str, Any]] = Field(
        None,
        description="要素提取模块配置，如正则或类型列表"
    )
    rules: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="逻辑分析规则配置"
    )


class OutputSpec(MarkdownValueMixin, BaseModel):
    summary_template: str = Field(..., description="摘要模板")
    metadata_schema: Dict[str, Any] = Field(
        ...,
        description="元数据字段及校验规则"
    )
    confidence_rules: Dict[str, str] = Field(
        ...,
        description="置信度阈值规则"
    )


class QaGeneration(MarkdownValueMixin, BaseModel):
    description: str = Field(..., description="QA 生成描述")
    count: int = Field(..., description="需要生成的问答对数量")
    qa_schema: Dict[str, str] = Field(
        ...,
        description="问答对字段名称及类型"
    )


class QaPair(MarkdownValueMixin, BaseModel):
    question: str = Field(..., description="生成的问题文本")
    answer: str = Field(..., description="模型的回答文本")
    reference: str = Field(..., description="答案引用来源标识")


class ChunkExampleOutput(MarkdownValueMixin, BaseModel):
    summary: str = Field(..., description="示例输出摘要")
    metadata: Dict[str, Any] = Field(..., description="示例输出元数据")
    confidence: float = Field(..., description="示例输出置信度")
    qa_pairs: List[QaPair] = Field(..., description="示例生成的问答对列表")


class SelectNeedDBModel(BaseModel):
    db_id: int = Field(..., description="数据库id")
    tables: list[str] = Field(..., description="数据库中需要使用的表名")


class SelectDBModel(BaseModel):
    need_db: list[SelectNeedDBModel] = Field(..., description="[{'db_id': db_id, 'tables': [table_name, table_name]}]")


class MakeSQLModel(BaseModel):
    sql: str = Field(..., description="SQL查询语句,必须根据提供的表结构生成")
    title: str = Field(..., description="title: 为这次查询起个标题")


class SelectDataCenterModel(BaseModel):
    db_id: list[int] = Field(..., description="数据源id")


class OptKnowledgeModel(BaseModel):
    sentences: list[str] = Field(..., description="用于继续检索知识库的5句话")


class FieldTidyUPModel(BaseModel):
    name: str = Field(..., description="根据信息推断出字段的名称或描述")




# ---------------- Enums ----------------
ColumnType = Literal["TEXT", "INTEGER", "DOUBLE", "DATE", "TIMESTAMP", "BOOLEAN", "UNKNOWN"]
ChartType = Literal["pie", "gantt", "flowchart", "timeline", "journey", "er", "class"]


# ---------------- Schema Models ----------------
class ColumnSchema(BaseModel):
    name: str = Field(..., description="列名")
    type: ColumnType = Field(..., description="推断/声明的数据类型")


class InferredSchema(BaseModel):
    table_name: str = Field(..., description="用于 DuckDB SQL 的表名")
    columns: List[ColumnSchema] = Field(..., description="列定义列表")
    time_columns: List[str] = Field(default_factory=list, description="时间相关列")
    primary_key_candidates: List[str] = Field(default_factory=list, description="主键候选")


class Insight(BaseModel):
    title: str = Field(..., description="洞见标题")
    method: str = Field(..., description="口径说明（如何计算/聚合）")
    evidence: str = Field(..., description="关键证据数字/范围/对比")
    limitation: Optional[str] = Field("", description="该洞见的边界/不足")


class Chart(BaseModel):
    type: ChartType = Field(..., description="Mermaid 图类型")
    title: str = Field(..., description="图表标题")
    mapping: str = Field(..., description="字段→图元素的映射规则")
    scenario: str = Field(..., description="该图适用场景一句话")
    code: str = Field(..., description="Mermaid 源码（不含围栏）")




class QuizItem(BaseModel):
    question: str = Field(..., description="与样本数据强相关、可搜索、可验证的问题")
    sql: str = Field(..., description="合法 DuckDB SQL（仅限 SELECT），针对 InferredSchema.table_name")
    why: str = Field(..., description="该 SQL 如何回答问题/口径")
    remark: Optional[str] = Field("", description="备注/边界/假设")


class TableBlock(BaseModel):
    title: str = Field(..., description="表格标题")
    columns: List[str] = Field(..., description="列名数组")
    rows: List[List[str]] = Field(default_factory=list, description="二维行数据")


class MarkdownResponse(BaseModel):
    markdown: str = Field(..., description="返回一个markdown")


class HtmlResponse(BaseModel):
    html: str = Field(..., description="返回一个html")


class AnalysisOutput(BaseModel):
    """
    与提示约定的 Output Schema 一致。
    """
    summary: str = Field(..., description="整体数据与任务的简述说明")
    # inferred_schema: InferredSchema = Field(..., description="从样本推断/声明的表结构")
    charts: List[Chart] = Field(default_factory=list, description="1–3 个 Mermaid 图配置")
    quiz: List[QuizItem] = Field(default_factory=list, description="恰好 5 条可复检的检索问题")

    #
    # insights: List[Insight] = Field(default_factory=list, description="洞见列表（可为空）")
    # tables: List[TableBlock] = Field(default_factory=list, description="用于佐证的表格")
    # limitation: Optional[str] = Field("", description="整体边界/不足/风险")

    def to_markdown(self) -> str:
        """
        生成 Markdown 格式报告：
        1. 根据字段 description 生成标题和说明
        2. 保留 Mermaid 图表的原始格式
        3. 其他字段以适合阅读的方式格式化
        """
        parts = []

        # 1. 添加标题和整体说明
        parts.append(f"# 数据分析报告\n\n")

        # 2. 添加摘要
        parts.append("## 整体数据与任务的简述说明\n")
        parts.append(f"{self.summary}\n")

        # 3. 添加推断的表结构
        # parts.append("## 从样本推断/声明的表结构\n")
        # parts.append(f"```json\n{json.dumps(self.inferred_schema.dict(), ensure_ascii=False, indent=2)}\n```\n")

        # # 4. 添加洞见
        # if self.insights:
        #     parts.append("\n## 洞见列表\n")
        #     for i, insight in enumerate(self.insights, 1):
        #         parts.append(f"{i}. {insight.description}\n")

        # 5. 添加 Mermaid 图表（保留原格式）
        if self.charts:
            parts.append(f"\n##  分析图表\n")

            for c in self.charts:
                parts.append(f"\n### {c.title}-{c.scenario}\n")
                parts.append(f"\n\n```mermaid\n{c.code}\n```\n\n")

        # 6. 添加测验问题
        if self.quiz:
            parts.append(f"\n## 复检的检索问题参考\n")
            for i, q in enumerate(self.quiz, 1):
                parts.append(f"{i}. {q.question}\n")

        # # 7. 添加表格数据
        # if self.tables:
        #     parts.append(f"\n## {self.__fields__['tables'].field_info.description}\n")
        #     for table in self.tables:
        #         parts.append(f"```json\n{json.dumps(table.dict(), ensure_ascii=False, indent=2)}\n```\n")

        # # 8. 添加限制说明
        # if self.limitation:
        #     parts.append(f"\n## {self.__fields__['limitation'].field_info.description}\n")
        #     parts.append(f"{self.limitation}\n")

        return "\n".join(parts)


LevelType = Literal[1,2,3]


class NoteVulModel(BaseModel):
    vuln_name: str = Field(..., description="简明扼要的漏洞名称（如：SQL注入/XSS/越权等）")
    vuln_level: LevelType = Field(..., description="漏洞危害等级（高危:3/中危:2/低危:1）")
    plan_item: str = Field(..., description="关联的测试计划或项目名称")
    url: str = Field(..., description="存在漏洞的完整URL地址")
    vuln_position: str = Field(..., description="漏洞具体位置（如：登录接口/用户ID参数等）")
    vuln_impact: str = Field(..., description="漏洞影响范围（如：所有用户/管理员权限等）")
    risk_desc: str = Field(..., description="漏洞风险的技术原理和实际危害")
    vuln_evidence: str = Field(..., description="漏洞复现步骤（含请求响应截图/代码片段）")
    fix_suggestion: str = Field(..., description="具体修复建议（含代码/配置修改方案）")
    reference_link: Optional[str] = Field("", description="相关CVE/参考文档链接（逗号分隔）")


class NoteVulInfosModel(BaseModel):
    vul_infos: List[NoteVulModel] = Field(..., description="从笔记中整理的所有漏洞项的详细信息列表")


class DocAnalysisKeyInfoModel(BaseModel):
    title: str = Field(..., description="信息名称")
    description: str = Field(..., description="信息描述")


class DocCorrection(BaseModel):
    original_text: str = Field(..., description="原文")
    corrected_text: str = Field(..., description="修改后的文字")


class DocObjectReference(BaseModel):
    precise_language: str = Field(..., description="精简的语言")
    target_object: str = Field(..., description="指向的对象")


class DocAnalysisModel(BaseModel):
    summary: str = Field(..., description="总结")
    topic: str = Field(..., description="内容主题")
    key_info: List[DocAnalysisKeyInfoModel] = Field(..., description="关键信息")
    global_info: List[DocAnalysisKeyInfoModel] = Field(..., description="全局信息")
    corrections: List[DocCorrection] = Field(..., description="纠错")
    core_idea: str = Field(..., description="核心思想")
    formulas: List[str] = Field(..., description="公式列表")
    object_references: List[DocObjectReference] = Field(..., description="对象引用列表")
    content: str = Field(..., description="上下文")


class FinishSearchInputModel(BaseModel):
    search_input: str = Field(..., description="整理后的搜索输入")

class FinishSearchInfoModel(BaseModel):
    search_result: str = Field(..., description="整理后的搜索输出")
