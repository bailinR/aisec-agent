#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/3/29 10:30
# @Author  : GuJR
# @Site    : 
# @File    : knowledge.py
from flask import Blueprint, g
from flask_restful import Api, Resource
from form_validate import form_validate

from aisec_agent.logic.knowledge.corpus.logic import CorpusCRUDLogic, LLMCorpusBase
from aisec_agent.logic.knowledge.database.es import ElasticsearchHelper
from aisec_agent.logic.knowledge.logic import PreFileLogic, KnowledgeCRUDLogic, PublishKBFile
from aisec_agent.model.typing import KnowledgeFileForm, Ret, KnowledgeSearchForm, KnowledgeQueryForm, \
    EmbeddingContentForm, FileIDForm, KnowledgeForm, CreateKnowledgeForm, CorpusImportForm, \
    CorpusSearchForm, CorpusReviseForm
import logging

knowledge = Blueprint('knowledge', __name__, url_prefix='/api/v0.1/knowledge')
api_rest = Api(knowledge)
logger = logging.getLogger(__name__)


@api_rest.resource('/file')
class PreFileAPI(Resource):

    @form_validate(KnowledgeFileForm)
    def post(self):
        """接收Minio文件路径，并将任务信息写入Redis队列"""
        form: KnowledgeFileForm = g.form
        PublishKBFile().task_publish(form)
        return Ret(data={'file_id': form.file_id, 'message': '文件已加入处理队列'}).dict()

    @form_validate(FileIDForm, mode=["query"])
    def get(self):
        """查询任务状态"""
        form: FileIDForm = g.form
        data = PublishKBFile().task_status(form.file_id)
        return Ret(data=data).dict()

    @form_validate(KnowledgeForm)
    def delete(self):
        """根据文件id删除知识库内容"""
        form: KnowledgeForm = g.form
        doc = {"term": {"file_id": form.file_id}}
        ElasticsearchHelper().delete_by_query(doc, form.topic)
        return Ret().dict()

    @form_validate(CreateKnowledgeForm)
    def put(self):
        """创建es数据库主题"""
        result = KnowledgeCRUDLogic().create_knowledge(**g.form.dict())
        return Ret(data=result).dict()


@api_rest.resource('/search')
class SearchAPI(Resource):

    @form_validate(KnowledgeSearchForm)
    def post(self):
        form: KnowledgeSearchForm = g.form
        result = PreFileLogic().search_knowledge(form.question, form.topics, form.search_conf)
        return Ret(data=result).dict()

    @form_validate(KnowledgeQueryForm, mode="query")
    def get(self):
        form: KnowledgeQueryForm = g.form
        result = PreFileLogic().page_knowledge(form.topic, form.file_id, form.page, form.page_size)
        return Ret(data=result).dict()


@api_rest.resource('/content')
class EmbeddingContentAPI(Resource):
    @form_validate(EmbeddingContentForm)
    def post(self):
        """新增单条记录"""
        form: EmbeddingContentForm = g.form
        try:
            KnowledgeCRUDLogic().add_content(form.content, form.topic, form.file_name, form.file_id)
            return Ret().dict()
        except Exception as e:
            return Ret(err_no=500, msg=str(e)).dict()

    @form_validate(EmbeddingContentForm)
    def patch(self):
        """更新单条记录"""
        form: EmbeddingContentForm = g.form
        try:
            KnowledgeCRUDLogic().revise_content(form.id, form.topic, form.content)
            return Ret().dict()
        except Exception as e:
            return Ret(err_no=500, msg=str(e)).dict()

    @form_validate(EmbeddingContentForm)
    def delete(self):
        """删除单条记录"""
        form: EmbeddingContentForm = g.form
        try:
            KnowledgeCRUDLogic().delete_content(form.id, form.topic)
            return Ret().dict()
        except Exception as e:
            return Ret(err_no=500, msg=str(e)).dict()


## 备份一下
# @api_rest.resource('/corpus')
# class CorpusAPI(Resource):
#     @form_validate(CorpusImportForm)
#     def put(self):
#         form: CorpusImportForm = g.form
#         LLMCorpusBase().import_es_data(form.topic, form.file_id, form.ranger)
#         return Ret().dict()
#
#     @form_validate(CorpusSearchForm)
#     def post(self):
#         form: CorpusSearchForm = g.form
#         result = LLMCorpusBase().search_es_data(form.question, form.topics)
#         return Ret(data=result).dict()

@api_rest.resource('/corpus')
class CorpusAPI(Resource):
    @form_validate(CorpusImportForm)
    def put(self):
        form: CorpusImportForm = g.form
        LLMCorpusBase().import_redis_data(form.topic)
        return Ret().dict()

    @form_validate(CorpusSearchForm)
    def post(self):
        form: CorpusSearchForm = g.form
        result = LLMCorpusBase().search_redis_data(form.question, form.topics)
        return Ret(data=result).dict()


@api_rest.resource('/corpus/config')
class CorpusCRUDAPI(Resource):
    @form_validate(CorpusReviseForm)
    def post(self):
        form: CorpusReviseForm = g.form
        try:
            CorpusCRUDLogic().force_replace_bulk(form.topic, form.corpus)
            return Ret().dict()
        except Exception as e:
            return Ret(err_no=500, msg=str(e)).dict()
