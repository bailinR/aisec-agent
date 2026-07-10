#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/3/15 17:25
# @Author  : GuJR
# @Site    : 
# @File    : typing.py
from typing import Any, Dict

from pydantic import BaseModel, Field
from pydantic.generics import GenericModel
from werkzeug.datastructures import FileStorage


class Ret(GenericModel):
    err_no: int = 0
    msg: str = 'success'
    data: Any = None


class BaseLLMChatForm(BaseModel):
    question: str = Field(..., description='问题')
    stream: bool = Field(False, description='是否流式返回')


class HRDataSplitForm(BaseLLMChatForm):
    datas: list = Field(..., description='人事数据')


class LLMChatAgentForm(BaseLLMChatForm):
    agent_id: str = Field(..., description='prompt_id')
    topics: list = Field([], description='主题名称/知识库名称')

class DuckQueryForm(BaseModel):
    object_name: str = Field(..., description='文件名')
    # table_name: str = Field(..., description='别名')
    format: str = Field("csv" , description='格式化')


from typing import Literal, List

class LLMChatForm(LLMChatAgentForm):
    url: str = Field(..., description='llm url')
    key: str = Field(..., description='llm key')
    function_name: str = Field(..., description='llm 来源')
    model_name: str = Field(..., description='模型名称')
    agent_func: str = Field(None, description='agent方法标识')
    padding_json: list = Field([], description='填充模板的json')
    max_len_input: int = Field(..., description='最大输入长度')
    file_name: str = Field(None, description='文件名称')
    file_content: str = Field(None, description='文件内容')
    file_path: List[Dict] = Field([], description='文件路径')
    prompt: str = Field(None, description='prompt')
    history: list = Field([], description='聊天记录')
    imgs: list = Field([], description='图片解析为文本后的内容')
    uid: str = Field(None, description='用户id')
    sid: str = Field(None, description='会话id')
    aid: str = Field(None, description='应用id')
    duck: bool = Field(False, description='是否使用数据分析')
    source: bool = Field(False, description='是否多源数据查询')
    trans: str = Field(None, description='翻译指定语言 如中文')
    deep_search: bool = Field(False, description='是否多源联网搜索')
    report_typ: Literal["html", "markdown", "ppt", "pptlist", "ppt_json"] = Field(None,
                                                                                  description="生成报告的文件类型")


class LLMAgentForm(BaseLLMChatForm):
    url: str = Field(..., description='llm url')
    key: str = Field(..., description='llm key')
    function_name: str = Field(..., description='llm 来源')
    model_name: str = Field(..., description='模型名称')
    max_len_input: int = Field(..., description='最大输入长度')
    file_name: str = Field(None, description='文件名称')
    file_content: str = Field(None, description='文件内容')
    file_path: List[Dict] = Field([], description='文件路径')
    prompt: str = Field(None, description='prompt')
    history: list = Field([], description='聊天记录')


class FileIDForm(BaseModel):
    file_id: str = Field(..., description='文件id')


class TaskIDForm(BaseModel):
    task_id: str = Field(..., description='任务id')

class KnowledgeForm(FileIDForm):
    topic: str = Field(..., description='主题名称/知识库名称')


class CreateKnowledgeForm(BaseModel):
    topic: str = Field(..., description='主题名称/知识库名称')


class KnowledgeFileForm(KnowledgeForm):
    file_name: str = Field(..., description='文件名')
    file_path: str = Field(..., description='文件路径')
    bucket: str = Field(..., description='桶名')


class SearchConfForm(BaseModel):
    top_k: int = Field(None, description='主题名称/知识库名称')
    expansion_factor: int = Field(None, description='扩展系数')

    class Config:
        from_attributes = True


class KnowledgeSearchForm(BaseModel):
    question: str = Field(..., description='文件名')
    search_conf: SearchConfForm = Field(None, description='搜索配置')
    topics: list = Field(..., description='主题名称/知识库名称')


class KnowledgeQueryForm(BaseModel):
    file_id: str = Field(..., description='文件id')
    topic: str = Field(..., description='主题名称/知识库名称')
    page: int = Field(default=1, ge=1, description='页码，默认为1')
    page_size: int = Field(default=10, ge=1, le=100, description='每页数量，默认为10，最小为1，最大为100')
    key_word: str = Field(None, description='关键词')


class EmbeddingContentForm(BaseModel):
    id: str = Field(None, description='记录id')
    topic: str = Field(None, description='主题名称/知识库名称')
    content: str = Field(None, description='需要被量化的文字')
    file_id: str = Field(None, description='文件id')
    file_name: str = Field(None, description='文件名称')


class AnalysisLogForm(BaseModel):
    question: str = Field(..., description='问题')
    stream: bool = Field(..., description='是否流式返回')
    topics: list = Field([], description='主题名称/知识库名称')
    file_name: str = Field(..., description='文件名')
    file: FileStorage = Field(..., description='文件')
    prompt_id: str = Field(..., description='prompt的uuid')

    class Config:
        arbitrary_types_allowed = True


class AssetsVulnForm(BaseModel):
    content: str = Field(..., description='资产信息')


class ASRToTextForm(BaseModel):
    file: FileStorage = Field(..., description='文件')

    class Config:
        arbitrary_types_allowed = True



class AgentForm(BaseModel):
    content: str = Field(..., description='输入文本')
    function_name: str = Field(None, description='调用llm方法名称')
    url: str = Field(None, description='调用llm链接')
    model_name: str = Field(None, description='调用模型名称')
    key: str = Field(None, description='调用模型密钥')

    class Config:
        json_exclude_none = True

    def dict(self, *args, **kwargs):
        kwargs.setdefault('exclude_none', True)
        return super().dict(*args, **kwargs)


class LLMConfigAddForm(BaseModel):
    url: str = Field(..., description='api url')
    key: str = Field(None, description='api key')
    function_name: str = Field(..., description='api来源')
    model_name: str = Field(..., description='模型源类型')
    type: str = Field(None, description='模型源类型')
    max_len_input: int = Field(..., description='最大输入长度')
    model_type: str = Field(None, description='模型类型')
    user_id: int = Field(..., description='用户id')
    is_public: int = Field(..., description='是否公开')


class VoiceRetForm(BaseModel):
    bucket: str = Field("agents", description='桶名')
    voice_files: list[str] = Field(..., max_length=2, description='选择音频文件')
    text: str = Field(..., description='文本')
    task_id: str = Field(..., description='任务id')


class LLMConfigEditForm(BaseModel):
    id: int = Field(..., description='llm id')
    url: str = Field(None, description='api url')
    key: str = Field(None, description='api key')
    function_name: str = Field(None, description='api来源')
    model_name: str = Field(None, description='模型名称')
    type: str = Field(None, description='模型源类型')
    max_len_input: int = Field(None, description='最大输入长度')
    model_type: str = Field(None, description='模型类型')
    is_public: int = Field(None, description='是否公开')
    user_id: int = Field(..., description='创建者id')


class LLMConfigGetForm(BaseModel):
    id: int = Field(None, description='llm id')
    user_id: int = Field(..., description='创建者id')


class PromptConfigGetForm(BaseModel):
    agent_id: str = Field(..., description='agent id')


class PromptConfigEditForm(PromptConfigGetForm):
    prompt: str = Field(..., description='修改后的prompt')


class ParseFirewallLogForm(BaseModel):
    text: str = Field(..., description='AI需要处理的文本')


class MCPToolsForm(BaseModel):
    services: list[dict] = Field(..., description='远程MCP服务列表')


class CallMCPForm(MCPToolsForm):
    question: str = Field(..., description='用户问题')
    tool_name: str = Field(None, description='工具名称')


class CorpusImportForm(BaseModel):
    topic: str = Field(..., description='es主题名称')


class CorpusSearchForm(BaseModel):
    topics: list = Field(None, description='es主题名称')
    question: str = Field(..., description='用户问题')


class CorpusReviseForm(BaseModel):
    id: str = Field(None, description='记录id')
    topic: str = Field(None, description='主题名称/知识库名称')
    corpus: list = Field(None, description='语料')


class Image2TextForm(BaseModel):
    image_base64: str = Field(..., description='base64编码的图片内容')


class FieldTidyUPForm(BaseModel):
    db_id: int = Field(..., description='数据库id')
    flag: bool = Field(..., description='是否立即更新的标识')


class PentestSummaryForm(BaseModel):
    plan: str = Field(..., description='计划名称')
    note: str = Field(..., description='笔记内容')