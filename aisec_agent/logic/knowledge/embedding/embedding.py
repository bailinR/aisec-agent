# !/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/3/29 12:32
# @Author  : GuJR
# @Site    :
# @File    : embedding.py
from typing import Union, List, Optional, Dict, Any
import numpy as np
import onnxruntime
from onnxruntime import InferenceSession, SessionOptions, GraphOptimizationLevel, ExecutionMode
from transformers import AutoTokenizer
from aisec_agent.config import JINA_MODEL_ONNX, JINA_MODEL, DEFAULT_MODEL_PATH
from aisec_agent.model.define import BaseHandler
from aisec_agent.model.enumerate import TaskType
import logging
import traceback
import requests


class BaseEmbedding(BaseHandler):
    """Embedding模型抽象基类"""

    def _normalize_embeddings(self, embeddings: np.ndarray) -> np.ndarray:
        """L2归一化"""
        return embeddings / np.linalg.norm(embeddings, ord=2, axis=1, keepdims=True)

    def encode(self, base_url: str, model_name: str, texts: Union[str, List[str]], **kwargs) -> np.ndarray:
        """将文本转换为向量表示"""
        pass


class JinaEmbedding(BaseEmbedding):
    """Jina Embedding 模型封装类"""

    def _init(self):
        """初始化Jina Embedding模型"""
        try:
            # 从配置获取模型路径
            model_path = JINA_MODEL_ONNX
            logging.info(f"加载嵌入模型: {model_path}")

            # 加载tokenizer
            tokenizer_path = JINA_MODEL
            self.tokenizer = AutoTokenizer.from_pretrained(tokenizer_path)

            # 配置ONNX Session，优先使用GPU
            providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']

            # 检查CUDA是否可用
            gpu_available = False
            try:
                if 'CUDAExecutionProvider' in onnxruntime.get_available_providers():
                    gpu_available = True
                    logging.warning("未检测到GPU，将使用CPU进行嵌入计算")
            except Exception as e:
                logging.warning(f"获取设备信息失败: {str(e)}，将使用默认设备")

            # 创建推理会话，如果GPU不可用则只使用CPU
            session_options = SessionOptions()
            session_options.enable_profiling = False
            session_options.graph_optimization_level = GraphOptimizationLevel.ORT_ENABLE_ALL
            session_options.execution_mode = ExecutionMode.ORT_PARALLEL

            # 记录可用的提供程序
            logging.info(f"可用的ONNX执行提供程序: {onnxruntime.get_available_providers()}")

            if gpu_available:
                self._session = InferenceSession(
                    model_path,
                    providers=providers,
                    provider_options=[{'device_id': 0}],
                    sess_options=session_options
                )
                # 输出实际使用的执行提供程序
                logging.info(f"ONNX Session实际使用的提供程序: {self._session.get_providers()}")
            else:
                self._session = InferenceSession(
                    model_path,
                    providers=['CPUExecutionProvider'],
                    sess_options=session_options
                )

            logging.info("Jina Embedding模型加载完成")

        except Exception as e:
            logging.error(f"加载嵌入模型失败: {str(e)}")
            traceback.print_exc()
            raise

    @property
    def session(self) -> InferenceSession:
        """获取ONNX会话"""
        return self._session

    @property
    def tokenizer(self) -> AutoTokenizer:
        """获取tokenizer"""
        return self._tokenizer

    @tokenizer.setter
    def tokenizer(self, value):
        """设置tokenizer"""
        self._tokenizer = value

    @staticmethod
    def _mean_pooling(model_output: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
        """
        对模型输出进行平均池化

        Args:
            model_output: 模型输出的token embeddings
            attention_mask: 注意力掩码

        Returns:
            池化后的embeddings
        """
        token_embeddings = model_output
        input_mask_expanded = np.expand_dims(attention_mask, axis=-1)
        input_mask_expanded = np.broadcast_to(input_mask_expanded, token_embeddings.shape)
        sum_embeddings = np.sum(token_embeddings * input_mask_expanded, axis=1)
        sum_mask = np.clip(np.sum(input_mask_expanded, axis=1), a_min=1e-9, a_max=None)
        return sum_embeddings / sum_mask

    def encode(self, texts: Union[str, List[str]], task_type: str = 'retrieval.query',
               batch_size: int = 32) -> np.ndarray:
        """将文本转换为向量表示"""
        # 确保输入为列表格式
        if isinstance(texts, str):
            texts = [texts]

        # 验证任务类型
        if task_type not in TaskType.to_dict():
            raise ValueError(f"不支持的任务类型: {task_type}. 支持的类型: {list(TaskType.to_dict().keys())}")

        total_texts = len(texts)
        logging.info(f"开始处理 {total_texts} 条文本的向量转换，批次大小: {batch_size}")

        all_embeddings = []

        # 批处理
        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i:i + batch_size]

            # 对输入文本进行tokenize处理
            inputs = self.tokenizer(batch_texts, padding=True, truncation=True, max_length=1024, return_tensors="np")

            # 准备模型输入
            onnx_inputs = {
                'input_ids': inputs['input_ids'].astype(np.int64),
                'attention_mask': inputs['attention_mask'].astype(np.int64),
                'task_id': np.array(TaskType.to_dict()[task_type], dtype=np.int64)
            }

            # 执行推理
            outputs = self.session.run(None, onnx_inputs)[0]

            # 处理输出
            embeddings = self._mean_pooling(outputs, inputs['attention_mask'])
            normalized_embeddings = self._normalize_embeddings(embeddings)

            all_embeddings.append(normalized_embeddings)

            if (i + batch_size) % (batch_size * 10) == 0 or i + batch_size >= total_texts:
                logging.info(f"已处理 {min(i + batch_size, total_texts)}/{total_texts} 条文本")

        # 合并所有批次的结果
        result = np.vstack(all_embeddings)
        logging.info(f"向量转换完成，生成了 {result.shape[0]} 个维度为 {result.shape[1]} 的向量")

        return result


class OllamaEmbedding(BaseEmbedding):
    """Ollama Embedding 模型封装类"""

    def __init__(self, base_url: str = None, model_name: str = None, **kwargs):
        """初始化Ollama Embedding

        Args:
            base_url: Ollama服务的基础URL
            model_name: 模型名称
            **kwargs: 其他配置参数
        """
        super().__init__()
        self.base_url = base_url
        self.model_name = model_name
        self.config = kwargs

    def encode(self, texts: Union[str, List[str]], **kwargs) -> np.ndarray:
        """
        将文本转换为向量表示

        Args:
            texts: 单个文本或文本列表
            **kwargs: 包含base_url和model_name等参数

        Returns:
            文本的向量表示
        """
        # 从实例属性或kwargs中获取参数，kwargs优先级更高
        base_url = kwargs.get('base_url', self.base_url)
        model_name = kwargs.get('model_name', self.model_name)

        if not base_url or not model_name:
            raise ValueError("base_url和model_name参数是必需的，请在初始化时或调用encode时提供")

        if isinstance(texts, str):
            texts = [texts]

        all_embeddings = []

        for text in texts:
            try:
                response = requests.post(
                    f"{base_url}/api/embeddings",
                    json={
                        "model": model_name,
                        "prompt": text
                    },
                    timeout=30
                )
                response.raise_for_status()

                result = response.json()
                embedding = np.array(result["embedding"], dtype=np.float32)
                all_embeddings.append(embedding)

            except Exception as e:
                logging.error(f"Ollama embedding请求失败: {e}")
                raise

        result = np.vstack(all_embeddings) if len(all_embeddings) > 1 else np.array([all_embeddings[0]])
        result = self._normalize_embeddings(result)
        return result


class LMStudioEmbedding(BaseEmbedding):
    """LM Studio Embedding 模型封装类"""

    def __init__(self, base_url: str = None, model_name: str = None, **kwargs):
        """初始化LM Studio Embedding

        Args:
            base_url: LM Studio服务的基础URL
            model_name: 模型名称
            **kwargs: 其他配置参数
        """
        super().__init__()
        self.base_url = base_url
        self.model_name = model_name
        self.config = kwargs

    def encode(self, texts: Union[str, List[str]], **kwargs) -> np.ndarray:
        """
        将文本转换为向量表示

        Args:
            texts: 单个文本或文本列表
            **kwargs: 包含base_url和model_name等参数

        Returns:
            文本的向量表示
        """
        # 从实例属性或kwargs中获取参数，kwargs优先级更高
        base_url = kwargs.get('base_url', self.base_url)
        model_name = kwargs.get('model_name', self.model_name)

        if not base_url or not model_name:
            raise ValueError("base_url和model_name参数是必需的，请在初始化时或调用encode时提供")

        if isinstance(texts, str):
            texts = [texts]

        try:
            response = requests.post(
                f"{base_url}/v1/embeddings",
                headers={
                    "Content-Type": "application/json"
                },
                json={
                    "model": model_name,
                    "input": texts
                },
                timeout=30
            )
            response.raise_for_status()
            result = response.json()
            embeddings = [np.array(item["embedding"], dtype=np.float32) for item in result["data"]]
            result_array = np.vstack(embeddings) if len(embeddings) > 1 else np.array([embeddings[0]])
            result_array = self._normalize_embeddings(result_array)
            return result_array

        except Exception as e:
            logging.error(f"LM Studio embedding请求失败: {e}")
            raise


class EmbeddingFactory:
    """Embedding模型工厂类"""

    @staticmethod
    def create_embedding(provider: str = "jina", **kwargs) -> BaseEmbedding:
        """创建embedding模型实例"""
        provider = provider.lower()

        if provider == "jina":
            embedding = JinaEmbedding()
            embedding._init()
            return embedding
        elif provider == "ollama":
            return OllamaEmbedding()
        elif provider == "lm_studio":
            return LMStudioEmbedding()
        else:
            raise ValueError(f"不支持的embedding提供商: {provider}. 支持的类型: ['jina', 'ollama', 'lm_studio']")


class EmbeddingManager:
    """Embedding管理器，提供统一的接口"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化Embedding管理器

        Args:
            config: 配置字典，包含provider和相关参数
                   例如: {
                       "provider": "ollama",
                       "base_url": "http://localhost:11434",
                       "model_name": "bge-m3:latest"
                   }
                   如果为None，将尝试从系统默认配置中读取
        """
        # 获取最终配置：用户传入配置 > 系统默认配置 > 默认jina配置
        self.config = self._get_final_config(config)
        self.embedding_model = None
        self._initialize_embedding()

    def _get_system_default_config(self) -> Dict[str, Any]:
        """
        从系统配置中获取默认embedding模型配置

        Returns:
            系统默认配置字典，如果没有配置则返回空字典
        """
        try:
            import json
            import os
            config_file = DEFAULT_MODEL_PATH
            if os.path.exists(config_file):
                with open(config_file, 'r', encoding='utf-8') as f:
                    file_config = json.load(f)
                    system_config = file_config.get('embedding', {})
                    if system_config:
                        logging.info(f"从DefaultModelConfig.json读取到系统默认embedding配置: {system_config}")
                        return system_config
            else:
                logging.info(f"配置文件 {config_file} 不存在，使用默认配置")

        except Exception as e:
            logging.warning(f"读取系统默认embedding配置失败: {e}")

        return {}

    def _get_final_config(self, user_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        获取最终的配置，按优先级合并配置

        优先级：用户传入配置 > 系统默认配置 > 默认jina配置

        Args:
            user_config: 用户传入的配置

        Returns:
            最终的配置字典
        """
        # 默认配置（最低优先级）
        final_config = {"provider": "jina"}

        # 系统默认配置（中等优先级）
        system_config = self._get_system_default_config()
        if system_config:
            final_config.update(system_config)
            logging.info(f"使用系统默认embedding配置: {system_config}")

        # 用户传入配置（最高优先级）
        if user_config:
            final_config.update(user_config)
            logging.info(f"使用用户指定embedding配置: {user_config}")

        logging.info(f"最终embedding配置: {final_config}")
        return final_config

    def _initialize_embedding(self):
        """初始化embedding模型"""
        try:
            provider = self.config.get("provider", "jina")
            logging.info(f"初始化embedding模型，提供商: {provider}")
            provider_kwargs = {k: v for k, v in self.config.items() if k != "provider"}
            self.embedding_model = EmbeddingFactory.create_embedding(provider, **provider_kwargs)
            logging.info(f"Embedding模型初始化成功: {provider}")

        except Exception as e:
            logging.warning(f"初始化embedding模型失败: {e}，回退到默认Jina模型")
            try:
                self.embedding_model = EmbeddingFactory.create_embedding("jina")
                logging.info("成功回退到Jina embedding模型")
            except Exception as fallback_error:
                logging.error(f"回退到Jina模型也失败: {fallback_error}")
                raise

    def encode(self, texts: Union[str, List[str]], **kwargs) -> np.ndarray:
        """将文本转换为向量表示"""
        if self.embedding_model is None:
            raise RuntimeError("Embedding模型未初始化")

        merged_kwargs = self.config.copy()
        merged_kwargs.pop('provider', None)
        merged_kwargs.update(kwargs)

        return self.embedding_model.encode(texts, **merged_kwargs)