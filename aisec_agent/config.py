#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/3/15 16:37
# @Author  : GuJR
# @Site    : 
# @File    : config.py
from os import environ, getcwd, makedirs, path

from ugly_code.ex import load_json_config

CWD_PATH = getcwd()
CONTENT_PATH = path.join(CWD_PATH, 'content')
AI_MODEL_DIR = path.join(CWD_PATH, 'ai_model')
_local_cfg_file = path.join(CONTENT_PATH, 'config.json')
MANAGE_PROMPT_PATH = path.join(CONTENT_PATH, 'ManagePrompt.json')
DEFAULT_MODEL_PATH = path.join(CONTENT_PATH, 'DefaultModelConfig.json')
DAILY_INFO_PATH = path.join(CONTENT_PATH, 'QueryDaily.json')
PROMPT_FILE_PATH = path.join(CONTENT_PATH, 'prompts')
PROMPT_BACKUP_FILE_PATH = path.join(PROMPT_FILE_PATH, 'backup')
BASIS_FILE_PATH = path.join(CONTENT_PATH, 'basis')
IIC_MODEL = path.join(AI_MODEL_DIR, "ASR/iic/SenseVoiceSmall")
HR_TABLES_PATH = path.join(CONTENT_PATH, "HRTable")
JINA_MODEL = path.join(AI_MODEL_DIR, "embedding/jinaai/jina-embeddings-v3")
JINA_MODEL_ONNX = path.join(JINA_MODEL, "onnx/model_fp16.onnx")
BGE_MODEL = path.join(AI_MODEL_DIR, "embedding/bge/m3")
BGE_MODEL_ONNX = path.join(BGE_MODEL, "onnx/model.onnx")
EXPORT_PATH = path.join(CONTENT_PATH, "export")
DUCK_URL = 'http://10.11.17.156:86/canndy/'
DEEP_SEARCH_CONF = {
    "url": environ.get("DEEP_SEARCH_URL", "https://api.bochaai.com/v1/ai-search"),
    "key": environ.get("DEEP_SEARCH_API_KEY", "")
}
MAIN_CHAT_CONF = {}
AIDED_LLM_CONF = {}
VISION_LLM_CONF = {}
DB_URI = environ.get("AISEC_DB_URI", "mysql://user:password@127.0.0.1:3306/aisec?charset=utf8mb4")


# Elasticsearch配置
ES_CONFIG = {
    "hosts": [environ.get("AISEC_ES_HOST", "http://127.0.0.1:9200")],
    "auth": (environ.get("AISEC_ES_USER", "elastic"), environ.get("AISEC_ES_PASSWORD", "")),
    "use_ssl": False,
    "verify_certs": True
}

# 并行处理配置
PROCESS_CONFIG = {
    "max_workers": 4,
    "chunk_size": 1000
}
AK =""
SK=""
DEFAULT_REDIS = {
    "host": "127.0.0.1",
    "db": 11
}

MINIO_CNF = {
    "endpoint": environ.get("AISEC_MINIO_ENDPOINT", "127.0.0.1:9000"),
    "access_key": environ.get("AISEC_MINIO_ACCESS_KEY", ""),
    "secret_key": environ.get("AISEC_MINIO_SECRET_KEY", ""),
    "secure": False,
}

LOG_CONFIG = {
    'level': 20,
    'filename': 'worker.log',
    'filemode': "w+",
    'format': '%(asctime)s %(filename)s [line:%(lineno)d] %(levelname)s %(message)s',
    'datefmt': '%Y-%m-%d %H:%M:%S',

}

EMBEDDING_CONFIG = {
    "base_url": environ.get("AISEC_EMBEDDING_BASE_URL", "http://127.0.0.1:11434/api"),
    "model_name": "bge-m3",
    "dimension": 1024
}

REPORT_TEMPLATE_CONF = {

}

VOICE_MODEL_PATH = path.join(AI_MODEL_DIR, "TTS/microsoft/VibeVoice")
VOICE_MP3_PATH = path.join(AI_MODEL_DIR, "TTS/microsoft/VibeVoice/voicemodel")
DEFAULT_VOICE_MP3 = path.join(VOICE_MP3_PATH, "xwlb.mp3")

SQL_SERVER_TABLES = {
    "TY_EMBASIC":
        {
            "cn_table_name": "基本信息表",
            "description": "涵盖了员工从入职到离职的全生命周期信息，包括个人基本信息、工作信息、状态信息、联系信息等，可以支持人力资源管理的各项功能，如员工信息查询、状态跟踪、合同管理、统计报表等。"
        },
    "TY_EMP_EDUCATION": {
        "cn_table_name": "教育经历表",
        "description": "存储员工的完整教育经历数据，可用于学历分析、背景核查及人才统计"
    },
    "TY_EMP_WORK": {
        "cn_table_name": "工作履历表",
        "description": "存储员工的完整工作经历数据，可用于工作分析、背景核查及人才统计"
    },
    "TY_TUNEBRANCH": {
        "cn_table_name": "调科",
        "description": "存储员工的完整的调动信息,调动的原部门和现部门,调动时间等"
    },
    "TY_EMP_LEAVE": {
        "cn_table_name": "人员请假信息",
        "description": "存储员工的完整的请假信息"
    }
}
GENIE_API = "http://10.11.17.156:1603/v1"
AK="gNqrjlm6c9NBvzRXrA67"
SK="LGwk7ogHHErtKHnwXWiC"
DISABLED_WORKERS = ()
MCP_SERVICES = [
    {
        "url": "http://10.11.17.156:81/toolbox/mcp",
        "description": ""
    },
    {
        "url": "http://10.11.17.156:81/piggy/mcp",
        "description": ""
    }
]

for d in (
       CONTENT_PATH,  EXPORT_PATH

):
    if not path.exists(d):
        makedirs(d, 0o755)
# ----------分割线--------(config.json替代config.py的值要写在这里以上)
if path.exists(_local_cfg_file):
    load_json_config(globals(), _local_cfg_file)
