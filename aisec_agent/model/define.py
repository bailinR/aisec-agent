import importlib
import json
import logging
from typing import Dict, Any, Type
import inspect
from .tools import Singleton
import pymysql
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.session import Session
import contextlib
from ..config import DB_URI, DEFAULT_REDIS
import inspect as py_inspect
from sqlalchemy import inspect
import redis
from redis.exceptions import RedisError


class BaseHandler(metaclass=Singleton):
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self._init()

    def _init(self):
        pass


pymysql.install_as_MySQLdb()
__author__ = 'Memory_Leak<yuz@cnns.net>'

Base = declarative_base()
engine = create_engine(DB_URI, pool_size=20, pool_recycle=600)

DBSession = sessionmaker(bind=engine)

@contextlib.contextmanager
def session_ctx(begin: bool = False) -> 'ContextManager[Session]':
    session = DBSession()
    try:
        if begin:
            session.begin()
        yield session
    except Exception as e:
        logging.exception("session ctx exception %s", e.__class__.__name__)
        raise e
    finally:
        session.close()


class RedisQueue:
    """Redis队列实现类"""

    def __init__(self, queue_name="file_process_queue", status_key="file_status", **redis_kwargs):
        """初始化Redis队列

        Args:
            queue_name: 队列名称
            status_key: 状态存储的key前缀
            redis_kwargs: Redis连接参数
        """
        self.queue_name = queue_name
        self.status_key = status_key
        self.redis_conn = redis.Redis(**redis_kwargs)
        self.logger = logging.getLogger(__name__)
        
        # 测试Redis连接
        try:
            self.redis_conn.ping()
            self.logger.info(f"Redis连接成功，队列名称: {queue_name}")
        except redis.ConnectionError as e:
            self.logger.error(f"Redis连接失败: {str(e)}")
            raise

    def rpush(self, key: str, task: str):
        self.redis_conn.rpush(key, task)

    def debug_queue(self):
        """调试方法：打印队列中的所有数据"""
        try:
            # 获取队列中的所有元素（不删除）
            items = self.redis_conn.lrange(self.queue_name, 0, -1)
            self.logger.info(f"队列 {self.queue_name} 中的所有数据:")
            for item in items:
                try:
                    data = json.loads(item)
                    self.logger.info(f"  - {data}")
                except json.JSONDecodeError:
                    self.logger.info(f"  - (无法解析JSON) {item}")
            return items
        except Exception as e:
            self.logger.error(f"调试队列失败: {str(e)}")
            return []

    def qsize(self):
        """获取队列大小"""
        size = self.redis_conn.llen(self.queue_name)
        self.logger.debug(f"队列大小: {size}")
        return size

    def is_empty(self):
        """判断队列是否为空"""
        return self.qsize() == 0

    def put(self, item):
        """将项目放入队列"""
        try:
            # 确保item是字典类型
            if not isinstance(item, dict):
                self.logger.error(f"任务格式错误，必须是字典类型: {type(item)}")
                return False
                
            # 序列化任务
            task_json = json.dumps(item)
            # 添加到队列
            self.redis_conn.rpush(self.queue_name, task_json)
            self.logger.info(f"成功添加任务到队列: {item}")
            return True
        except Exception as e:
            self.logger.error(f"添加任务失败: {str(e)}", exc_info=True)
            return False

    def get(self, block=True, timeout=None, count=1):
        """从队列获取项目

        Args:
            block: 队列为空时是否阻塞（不再使用）
            timeout: 阻塞超时时间（不再使用）
            count: 获取的项目数量

        Returns:
            list: 任务内容列表
        """
        try:
            # 先打印队列中的所有数据
            self.debug_queue()
            
            # 使用LPOP命令获取任务
            tasks = []
            for _ in range(count):
                item = self.redis_conn.lpop(self.queue_name)
                if item:
                    try:
                        task = json.loads(item)
                        tasks.append(task)
                        self.logger.info(f"成功获取任务: {task}")
                    except json.JSONDecodeError as e:
                        self.logger.error(f"JSON解析失败: {str(e)}, 原始数据: {item}")
                        continue
                else:
                    break
            
            if tasks:
                self.logger.info(f"成功获取{len(tasks)}个任务")
            else:
                self.logger.info("队列为空，未获取到任务")
                
            return tasks
            
        except Exception as e:
            self.logger.error(f"获取任务失败: {str(e)}", exc_info=True)
            return []

    def update_status(self, file_id: str, status: Dict[str, Any]):
        """更新文件处理状态

        Args:
            file_id: 文件ID
            status: 状态信息，包含progress和completed字段
        """
        try:
            key = f"{self.status_key}:{file_id}"
            self.redis_conn.set(key, json.dumps(status))
            self.redis_conn.expire(key, 86400)  # 24小时后过期
            self.logger.debug(f"更新状态成功: {file_id} - {status}")
        except Exception as e:
            self.logger.error(f"更新状态失败: {str(e)}")
            raise

    def get_status(self, file_id: str) -> Dict[str, Any]:
        """获取文件处理状态

        Args:
            file_id: 文件ID

        Returns:
            dict: 状态信息
        """
        try:
            key = f"{self.status_key}:{file_id}"
            data = self.redis_conn.get(key)
            if data:
                return json.loads(data)
            return {"progress": 0, "completed": False}
        except Exception as e:
            self.logger.error(f"获取状态失败: {str(e)}")
            return {"progress": 0, "completed": False}


class ModelAnalyzer(BaseHandler):
    def _init(self):
        self.model_info = {}

    def get_all_models(self, model_module_path: str = "aisec_agent.model.model") -> Dict[str, Dict]:
        """
        获取指定模块中所有的SQLAlchemy模型类及其属性信息

        Args:
            model_module_path: 模型模块的导入路径

        Returns:
            Dict: 表名到表信息的映射
        """
        try:
            # 动态导入模型模块
            module = importlib.import_module(model_module_path)

            # 获取所有模型类
            for name, obj in py_inspect.getmembers(module):
                if py_inspect.isclass(obj) and hasattr(obj, '__tablename__'):
                    table_info = self._get_table_info(obj)
                    relationship_info = self._get_relationship_info(obj)

                    self.model_info[obj.__tablename__] = {
                        'model_class': obj,
                        'description': obj.__doc__ or '',
                        'columns': table_info,
                        'relationships': relationship_info
                    }

            return self.model_info
        except Exception as e:
            raise Exception(f"Failed to analyze models: {str(e)}")

    def _get_table_info(self, model_class: Type) -> Dict[str, Dict[str, Any]]:
        """
        获取表的列信息
        """
        mapper = inspect(model_class)
        columns = {}

        for column in mapper.columns:
            columns[column.name] = {
                'type': str(column.type),
                'nullable': column.nullable,
                'primary_key': column.primary_key,
                'comment': getattr(column, 'comment', None)
            }

        return columns

    def _get_relationship_info(self, model_class: Type) -> Dict[str, Dict[str, Any]]:
        """
        获取表的关联关系信息
        """
        mapper = inspect(model_class)
        relationships = {}

        for rel_name, rel in mapper.relationships.items():
            target_model = rel.mapper.class_
            # 从模型类中找到原始relationship定义
            original_rel = getattr(model_class, rel_name, None)

            rel_info = {
                'target_model': target_model.__name__,
                'target_table': target_model.__tablename__,
                'type': self._get_relationship_type(rel),
                'back_populates': getattr(rel, 'back_populates', None),
                'uselist': getattr(rel, 'uselist', None),
                'cascade': str(rel.cascade),
                'description': getattr(original_rel, 'doc', '') if original_rel else ''
            }
            relationships[rel_name] = rel_info

        return relationships

    def _get_relationship_type(self, rel) -> str:
        """
        获取关联关系类型
        """
        if rel.direction.name == 'MANYTOONE':
            return '多对一'
        elif rel.direction.name == 'ONETOMANY':
            return '一对多'
        elif rel.direction.name == 'MANYTOMANY':
            return '多对多'
        elif rel.direction.name == 'ONETOONE':
            return '一对一'
        else:
            return rel.direction.name

class SmartQueryGenerator:
    def __init__(self, model_info: Dict):
        self.model_info = model_info

    def generate_table_info_str(self, select_tables: list | None) -> str:
        """生成表信息的字符串描述"""
        info_str = ""
        for table_name, info in self.model_info.items():
            if table_name and table_name not in select_tables:
                continue
            info_str += f"\n<{table_name}>\n"
            info_str += f"表名: {table_name}\n"
            info_str += f"描述: {info['description']}\n"
            info_str += "列信息:\n"
            for col_name, col_info in info['columns'].items():
                info_str += f"  - {col_name}: {col_info['type']}"
                if col_info['comment']:
                    info_str += f" ({col_info['comment']})"
                info_str += "\n"
            # for rel_name, rel_info in info['relationships'].items():
            #     info_str += f"  - {rel_name}: 关联到{rel_info['target_table']}"
            #     if rel_info['back_populates']:
            #         info_str += f" (back_populates:{rel_info['back_populates']})"
            #     if rel_info['type']:
            #         info_str += f" (type:{rel_info['type']})"
            #     info_str += "\n"
            info_str += f"</{table_name}>\n\n"
        return info_str

    def generate_class_methods_info(self, target_class) -> str:
        """
        生成类中所有方法的信息字符串描述

        Args:
            target_class: 要分析的类

        Returns:
            str: 包含类中所有方法信息的字符串
        """
        methods_list = []
        # 获取所有非内置方法
        methods = [method for method in dir(target_class) if not method.startswith('__')]

        for method_name in methods:
            # 获取方法对象
            method = getattr(target_class, method_name)

            # 确保是可调用的
            if not callable(method):
                continue

            # 获取文档字符串
            if method.__doc__:
                import textwrap
                doc = textwrap.dedent(method.__doc__).strip()
                methods_list.append(f"{method_name}({doc})")
            else:
                methods_list.append(f"{method_name}(无文档)")

        return "/".join(methods_list)

    def get_model_class(self, table_name: str) -> Type:
        """获取表对应的模型类"""
        return self.model_info[table_name]['model_class']


# """模型专用单例（不完善，目前无法释放rola框架量化后的模型）"""
# class BaseModelHandle:
#     _instance = None
#     _all_instances = set()
#     _required_memory_per_instance = 22  # 用于计算显存使用，判断是否需要释放单例
#
#     def __new__(cls, *args, **kwargs):
#         if cls._instance is None:
#             cls._instance = super(BaseModelHandle, cls).__new__(cls, *args, **kwargs)
#             cls._instance._init(*args, **kwargs)
#             cls._all_instances.add(cls._instance)
#         return cls._instance
#
#     def _init(self, *args, **kwargs):
#         self._check_and_free_memory()
#
#     def _check_and_free_memory(self):
#         required_memory = self._required_memory_per_instance * 1024 ** 3  # 转换为Bytes
#         max_memory = torch.cuda.get_device_properties(0).total_memory
#         free_memory = max_memory - (torch.cuda.memory_allocated() + torch.cuda.memory_reserved())
#         if free_memory < required_memory:
#             self._release_memory()
#
#     @classmethod
#     def get_all_instances(cls):
#         return cls._all_instances
#
#     def _release_memory(self):
#         self.get_all_instances().clear()
#         # 未完成：这里写检测单例是否在运行，如果没有则释放资源的代码，如果有就等待


class RedisHelper:
    def __init__(self):
        self.pool = redis.ConnectionPool(**DEFAULT_REDIS)
        self.redis = redis.Redis(connection_pool=self.pool)

    def pipeline(self):
        """返回Redis管道对象"""
        return self.redis.pipeline()

    def zadd(self, name, mapping, nx=False, xx=False, ch=False, incr=False):
        """有序集合添加操作"""
        try:
            return self.redis.zadd(name, mapping, nx=nx, xx=xx, ch=ch, incr=incr)
        except RedisError as e:
            raise RedisError(f"Redis zadd error: {str(e)}")

    def zscan(self, name, cursor=0, match=None, count=None):
        """有序集合扫描操作"""
        try:
            return self.redis.zscan(name, cursor=cursor, match=match, count=count)
        except RedisError as e:
            raise RedisError(f"Redis zscan error: {str(e)}")

    def close(self):
        """关闭连接池"""
        self.pool.disconnect()